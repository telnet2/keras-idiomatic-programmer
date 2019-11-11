# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# SE-ResNeXt (50, 101, 152)
# Paper: https://arxiv.org/pdf/1709.01507.pdf

import tensorflow as tf
from tensorflow.keras import Model, Input
from tensorflow.keras.layers import Conv2D, MaxPooling2D, BatchNormalization, ReLU, Dense, Add
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Reshape, Multiply, Lambda, Concatenate
from tensorflow.keras.regularizers import l2

class SEResNeXt(object):
    """ Construct a Squeeze & Excite Residual Next Convolution Neural Network """
    # Meta-parameter: number of filters in, out and number of blocks
    groups = { 50 : [ { 'filters_in': 128,  'filters_out' : 256,  'n_blocks': 3 },
                      { 'filters_in': 256,  'filters_out' : 512,  'n_blocks': 4 },
                      { 'filters_in': 512,  'filters_out' : 1024, 'n_blocks': 6 },
                      { 'filters_in': 1024, 'filters_out' : 2048, 'n_blocks': 3 } ],     # SE-ResNeXt50
               101 :[ { 'filters_in': 128,  'filters_out' : 256,  'n_blocks': 3 },
                      { 'filters_in': 256,  'filters_out' : 512,  'n_blocks': 4 },
                      { 'filters_in': 512,  'filters_out' : 1024, 'n_blocks': 23 },
                      { 'filters_in': 1024, 'filters_out' : 2048, 'n_blocks': 3 } ],     # SE-ResNeXt101
               152 :[ { 'filters_in': 128,  'filters_out' : 256,  'n_blocks': 3 },
                      { 'filters_in': 256,  'filters_out' : 512,  'n_blocks': 8 },
                      { 'filters_in': 512,  'filters_out' : 1024, 'n_blocks': 36 },
                      { 'filters_in': 1024, 'filters_out' : 2048, 'n_blocks': 3 } ]      # SE-ResNeXt152
             }

    # Meta-parameter: width of group convolution
    cardinality = 32
    # Meta-parameter: Amount of filter reduction in squeeze operation
    ratio = 16
    init_weights = 'he_normal'
    reg = l2(0.001)
    _model = None

    def __init__(self, n_layers, cardinality=32, ratio=16, input_shape=(224, 224, 3), n_classes=1000):
        """ Construct a Residual Next Convolution Neural Network
            n_layers   : number of layers
            cardinality: width of group convolution
            ratio      : amount of filter reduction in squeeze operation
            input_shape: the input shape
            n_classes  : number of output classes
        """
        # predefined
        if isinstance(n_layers, int):
            if n_layers not in [50, 101, 152]:
                raise Exception("SE-ResNeXt: Invalid value for n_layers")
            groups = self.groups[n_layers]
        # user defined
        else:
            groups = n_layers

        # The input tensor
        inputs = Input(shape=input_shape)

        # The Stem Group
        x = self.stem(inputs)

        # The Learner
        x = self.learner(x, groups=groups, cardinality=cardinality, ratio=ratio)

        # The Classifier 
        outputs = self.classifier(x, n_classes)

        # Instantiate the Model
        self._model = Model(inputs, outputs)

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, _model):
        self._model = _model

    def stem(self, inputs):
        """ Construct the Stem Convolution Group
            inputs : input vector
        """
        x = Conv2D(64, (7, 7), strides=(2, 2), padding='same', use_bias=False, 
                   kernel_initializer=self.init_weights, kernel_regularizer=self.reg)(inputs)
        x = BatchNormalization()(x)
        x = ReLU()(x)
        x = MaxPooling2D((3, 3), strides=(2, 2), padding='same')(x)
        return x

    def learner(self, x, init_weights=None, **metaparameters):
        """ Construct the Learner
            x          : input to the learner
            groups     : list of groups: filters in, filters out, number of blocks
        """
        groups = metaparameters['groups']

        # First ResNeXt Group (not strided)
        x = SEResNeXt.group(x, strides=(1, 1), init_weights=init_weights, **groups.pop(0), **metaparameters)

        # Remaining ResNeXt Groups
        for group in groups:
            x = SEResNeXt.group(x, init_weights=init_weights, **group, **metaparameters)
        return x

    @staticmethod
    def group(x, strides=(2, 2), init_weights=None, **metaparameters):
        """ Construct a Squeeze-Excite Group
            x          : input to the group
            strides    : whether projection block is strided
            n_blocks   : number of blocks in the group
        """
        n_blocks = metaparameters['n_blocks']

        # First block is a linear projection block
        x = SEResNeXt.projection_block(x, strides=strides, init_weights=init_weights, **metaparameters)

        # Remaining blocks are identity links
        for _ in range(n_blocks-1):
            x = SEResNeXt.identity_block(x, init_weights=init_weights, **metaparameters) 
        return x

    @staticmethod
    def squeeze_excite_block(x, init_weights=None, **metaparameters):
        """ Construct a Squeeze and Excite block
            x    : input to the block
            ratio : amount of filter reduction during squeeze
            reg   : kernel regularizer
        """  
        if 'ratio' in metaparameters:
            ratio = metaparameters['ratio']
        else:
            ratio = SEResNeXt.ratio
        if 'reg' in metaparameters:
            reg = metaparameters['reg']
        else:
            reg = SEResNeXt.reg

        if init_weights is None:
            init_weights = SEResNeXt.init_weights
            
        # Remember the input
        shortcut = x
    
        # Get the number of filters on the input
        filters = x.shape[-1]

        # Squeeze (dimensionality reduction)
        # Do global average pooling across the filters, which will the output a 1D vector
        x = GlobalAveragePooling2D()(x)
    
        # Reshape into 1x1 feature maps (1x1xC)
        x = Reshape((1, 1, filters))(x)
    
        # Reduce the number of filters (1x1xC/r)
        x = Dense(filters // ratio, activation='relu', use_bias=False, 
                  kernel_initializer=init_weights, kernel_regularizer=reg)(x)

        # Excitation (dimensionality restoration)
        # Restore the number of filters (1x1xC)
        x = Dense(filters, activation='sigmoid', use_bias=False,
                  kernel_initializer=init_weights, kernel_regularizer=reg)(x)

        # Scale - multiply the squeeze/excitation output with the input (WxHxC)
        x = Multiply()([shortcut, x])
        return x

    @staticmethod
    def identity_block(x, init_weights=None, **metaparameters):
        """ Construct a ResNeXT block with identity link
            x          : input to block
            filters_in : number of filters  (channels) at the input convolution
            filters_out: number of filters (channels) at the output convolution
            cardinality: width of cardinality layer
            reg        : kernel regularizer
        """ 
        filters_in  = metaparameters['filters_in']
        filters_out = metaparameters['filters_out']
        cardinality = metaparameters['cardinality']
        if 'reg' in metaparameters:
            reg = metaparameters['reg']
        else:
            reg = SEResNeXt.reg

        if init_weights is None:
            init_weights = SEResNeXt.init_weights
    
        # Remember the input
        shortcut = x

        # Dimensionality Reduction
        x = Conv2D(filters_in, kernel_size=(1, 1), strides=(1, 1), padding='same', use_bias=False,
                   kernel_initializer=init_weights, kernel_regularizer=reg)(shortcut)
        x = BatchNormalization()(x)
        x = ReLU()(x)

        # Cardinality (Wide) Layer (split-transform)
        filters_card = filters_in // cardinality
        groups = []
        for i in range(cardinality):
            group = Lambda(lambda z: z[:, :, :, i * filters_card:i * filters_card + filters_card])(x)
            groups.append(Conv2D(filters_card, kernel_size=(3, 3), strides=(1, 1), padding='same', use_bias=False,
                                 kernel_initializer=init_weights, kernel_regularizer=reg)(group))

        # Concatenate the outputs of the cardinality layer together (merge)
        x = Concatenate()(groups)
        x = BatchNormalization()(x)
        x = ReLU()(x)

        # Dimensionality restoration
        x = Conv2D(filters_out, kernel_size=(1, 1), strides=(1, 1), padding='same', use_bias=False,
                   kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        x = BatchNormalization()(x)
    
        # Pass the output through the squeeze and excitation block
        x = SEResNeXt.squeeze_excite_block(x, init_weights, **metaparameters)

        # Identity Link: Add the shortcut (input) to the output of the block
        x = Add()([shortcut, x])
        x = ReLU()(x)
        return x

    @staticmethod
    def projection_block(x, strides=1, init_weights=None, **metaparameters):
        """ Construct a ResNeXT block with projection shortcut
            x          : input to the block
            strides    : whether entry convolution is strided (i.e., (2, 2) vs (1, 1))
            filters_in : number of filters  (channels) at the input convolution
            filters_out: number of filters (channels) at the output convolution
            cardinality: width of cardinality layer
            reg        : kernel regularizer
        """ 
        filters_in  = metaparameters['filters_in']
        filters_out = metaparameters['filters_out']
        cardinality = metaparameters['cardinality']
        if 'reg' in metaparameters:
            reg = metaparameters['reg']
        else:
            reg = SEResNeXt.reg

        if init_weights is None:
            init_weights = SEResNeXt.init_weights
    
        # Construct the projection shortcut
        # Increase filters by 2X to match shape when added to output of block
        shortcut = Conv2D(filters_out, kernel_size=(1, 1), strides=strides, padding='same', 
                          kernel_initializer=init_weights)(x)
        shortcut = BatchNormalization()(shortcut)

        # Dimensionality Reduction
        x = Conv2D(filters_in, kernel_size=(1, 1), strides=(1, 1), padding='same', use_bias=False,
                   kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        x = BatchNormalization()(x)
        x = ReLU()(x)

        # Cardinality (Wide) Layer (split-transform)
        filters_card = filters_in // cardinality
        groups = []
        for i in range(cardinality):
            group = Lambda(lambda z: z[:, :, :, i * filters_card:i * filters_card + filters_card])(x)
            groups.append(Conv2D(filters_card, kernel_size=(3, 3), strides=strides, padding='same', use_bias=False,
                                 kernel_initializer=init_weights, kernel_regularizer=reg)(group))

        # Concatenate the outputs of the cardinality layer together (merge)
        x = Concatenate()(groups)
        x = BatchNormalization()(x)
        x = ReLU()(x)

        # Dimensionality restoration
        x = Conv2D(filters_out, kernel_size=(1, 1), strides=(1, 1), padding='same', use_bias=False,
                   kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        x = BatchNormalization()(x)
    
        # Pass the output through the squeeze and excitation block
        x = SEResNeXt.squeeze_excite_block(x, init_weights, **metaparameters)

        # Add the projection shortcut (input) to the output of the block
        x = Add()([shortcut, x])
        x = ReLU()(x)
        return x
    
    def classifier(self, x, n_classes):
        """ Construct the Classifier
            x         : input to the classifier
            n_classes : number of output classes
        """
        # Final Dense Outputting Layer 
        x = GlobalAveragePooling2D()(x)
        outputs = Dense(n_classes, activation='softmax', 
                        kernel_initializer=self.init_weights, kernel_regularizer=self.reg)(x)
        return outputs

# Example
# senet = SEResNeXt(50)
