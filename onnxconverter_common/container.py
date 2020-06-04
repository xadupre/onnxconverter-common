# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
###############################################################################

from onnx import helper
from .interface import ModelContainer


class RawModelContainer(object):
    '''
    This container is the carrier of the model we want to convert. It provides an abstract layer so that our parsing
    framework can work with models generated by different tools.
    '''

    def __init__(self, raw_model):
        self._raw_model = raw_model

    @property
    def raw_model(self):
        return self._raw_model

    @property
    def input_names(self):
        '''
        This function should return a list of strings. Each string corresponds to an input variable name.
        :return: a list of string
        '''
        raise NotImplementedError()

    @property
    def output_names(self):
        '''
        This function should return a list of strings. Each string corresponds to an output variable name.
        :return: a list of string
        '''
        raise NotImplementedError()


class CommonSklearnModelContainer(RawModelContainer):

    def __init__(self, sklearn_model):
        super(CommonSklearnModelContainer, self).__init__(sklearn_model)
        # Scikit-learn models have no input and output specified, so we create them and store them in this container.
        self._inputs = []
        self._outputs = []

    @property
    def input_names(self):
        return [variable.raw_name for variable in self._inputs]

    @property
    def output_names(self):
        return [variable.raw_name for variable in self._outputs]

    def add_input(self, variable):
        # The order of adding variables matters. The final model's input names are sequentially added as this list
        if variable not in self._inputs:
            self._inputs.append(variable)

    def add_output(self, variable):
        # The order of adding variables matters. The final model's output names are sequentially added as this list
        if variable not in self._outputs:
            self._outputs.append(variable)


# in case some this oc-common pkg works with some older onnxmltools?
class LightGbmModelContainer(CommonSklearnModelContainer):
    pass


class XGBoostModelContainer(CommonSklearnModelContainer):
    pass


class ModelComponentContainer(ModelContainer):
    '''
    In the conversion phase, this class is used to collect all materials required to build an ONNX GraphProto, which is
    encapsulated in a ONNX ModelProto.
    '''

    def __init__(self, target_opset):
        '''
        :param target_opset: number, for example, 7 for ONNX 1.2, and 8 for ONNX 1.3.
        :param targeted_onnx: A string, for example, '1.1.2' and '1.2'.
        '''
        # Inputs of ONNX graph. They are ValueInfoProto in ONNX.
        self.inputs = []
        # Outputs of ONNX graph. They are ValueInfoProto in ONNX.
        self.outputs = []
        # ONNX tensors (type: TensorProto). They are initializers of ONNX GraphProto.
        self.initializers = []
        # Intermediate variables in ONNX computational graph. They are ValueInfoProto in ONNX.
        self.value_info = []
        # ONNX nodes (type: NodeProto) used to define computation structure
        self.nodes = []
        # ONNX operators' domain-version pair set. They will be added into opset_import field in the final ONNX model.
        self.node_domain_version_pair_sets = set()
        # The targeted ONNX operator set (referred to as opset) that matches the ONNX version.
        self.target_opset = target_opset
        self.enable_optimizer = True

    def _make_value_info(self, variable):
        value_info = helper.ValueInfoProto()
        value_info.name = variable.full_name
        value_info.type.CopyFrom(variable.type.to_onnx_type())
        if variable.type.doc_string:
            value_info.doc_string = variable.type.doc_string
        return value_info

    def add_input(self, variable):
        '''
        Add our Variable object defined _parser.py into the the input list of the final ONNX model

        :param variable: The Variable object to be added
        '''
        self.inputs.append(self._make_value_info(variable))

    def add_output(self, variable):
        '''
        Add our Variable object defined _parser.py into the the output list of the final ONNX model

        :param variable: The Variable object to be added
        '''
        self.outputs.append(self._make_value_info(variable))

    def add_initializer(self, name, onnx_type, shape, content):
        '''
        Add a TensorProto into the initializer list of the final ONNX model

        :param name: Variable name in the produced ONNX model.
        :param onnx_type: Element types allowed in ONNX tensor, e.g., TensorProto.FLOAT and TensorProto.STRING.
        :param shape: Tensor shape, a list of integers.
        :param content: Flattened tensor values (i.e., a float list or a float array).
        '''
        if any(d is None for d in shape):
            raise ValueError('Shape of initializer cannot contain None')
        tensor = helper.make_tensor(name, onnx_type, shape, content)
        self.initializers.append(tensor)

    def add_value_info(self, variable):
        self.value_info.append(self._make_value_info(variable))

    def add_node(self, op_type, inputs, outputs, op_domain='', op_version=1, **attrs):
        '''
        Add a NodeProto into the node list of the final ONNX model. If the input operator's domain-version information
        cannot be found in our domain-version pool (a Python set), we may add it.

        :param op_type: A string (e.g., Pool and Conv) indicating the type of the NodeProto
        :param inputs: A list of strings. They are the input variables' names of the considered NodeProto
        :param outputs: A list of strings. They are the output variables' names of the considered NodeProto
        :param op_domain: The domain name (e.g., ai.onnx.ml) of the operator we are trying to add.
        :param op_version: The version number (e.g., 0 and 1) of the operator we are trying to add.
        :param attrs: A Python dictionary. Keys and values are attributes' names and attributes' values, respectively.
        '''

        if isinstance(inputs, str):
            inputs = [inputs]
        if isinstance(outputs, str):
            outputs = [outputs]
        if not isinstance(inputs, (list, tuple)) or not all(isinstance(s, str) for s in inputs):
            type_list = ','.join(list(str(type(s)) for s in inputs))
            raise ValueError('Inputs must be a list of string but get [%s]' % type_list)
        if not isinstance(outputs, (list, tuple)) or not all(isinstance(s, str) for s in outputs):
            type_list = ','.join(list(str(type(s)) for s in outputs))
            raise ValueError('Outputs must be a list of string but get [%s]' % type_list)
        for k, v in attrs.items():
            if v is None:
                raise ValueError('Failed to create ONNX node. Undefined attribute pair (%s, %s) found' % (k, v))

        try:
            node = helper.make_node(op_type, inputs, outputs, **attrs)
        except ValueError as e:
            for k, v in attrs.items():
                typ = set(type(_) for _ in v)
                if len(typ) > 1:
                    styp = typ
                    typ = {k: [] for k in typ}
                    for _ in v:
                        typ[type(_)].append(_)
                    rows = []
                    for kk, vv in typ.items():
                        if len(vv) > 3:
                            vv = vv[:3] + ['...']
                        rows.append("{}: {}".format(kk, ", ".join(map(str, vv))))
                    raise TypeError("Attribute '{}' mixes types {}\n{}."
                                    "".format(k, styp, "\n".join(rows)))
            raise e
        node.domain = op_domain

        self.node_domain_version_pair_sets.add((op_domain, op_version))
        self.nodes.append(node)
