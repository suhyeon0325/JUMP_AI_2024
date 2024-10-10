import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, Input, Model, regularizers
from tensorflow.keras.callbacks import ReduceLROnPlateau, ModelCheckpoint, EarlyStopping
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors, QED

try:
    tpu = tf.distribute.cluster_resolver.TPUClusterResolver()
    tf.config.experimental_connect_to_cluster(tpu)
    tf.tpu.experimental.initialize_tpu_system(tpu)
    strategy = tf.distribute.TPUStrategy(tpu)
    print("TPU initialized successfully.")
except ValueError:
    strategy = tf.distribute.get_strategy()
    print("Default strategy used")

class MyKerasRegressor(BaseEstimator, RegressorMixin):
    def __init__(self, build_fn, voxel_input_shape, rdkit_input_shape, smiles_input_shape, enc, max_smiles_len, optimizer='adam', dropout_rate=0.5, learning_rate=0.001, **kwargs):
        self.build_fn = build_fn
        self.optimizer = optimizer
        self.dropout_rate = dropout_rate
        self.learning_rate = learning_rate
        self.voxel_input_shape = voxel_input_shape
        self.rdkit_input_shape = rdkit_input_shape
        self.smiles_input_shape = smiles_input_shape
        self.enc = enc
        self.max_smiles_len = max_smiles_len
        self.kwargs = kwargs
        self.model = None
    
    def fit(self, X, y, callbacks=None, **fit_params):
        X_voxel, X_rdkit, X_smiles = X
        self.model = self.build_fn(
            voxel_input_shape=self.voxel_input_shape,
            rdkit_input_shape=self.rdkit_input_shape,
            smiles_input_shape=self.smiles_input_shape,
            enc=self.enc,
            max_smiles_len=self.max_smiles_len,
            optimizer=self.optimizer,
            dropout_rate=self.dropout_rate,
            learning_rate=self.learning_rate,
            **self.kwargs
        )
        self.model.compile(
            optimizer=tf.keras.optimizers.get(self.optimizer),
            loss='mean_squared_error',
            metrics=[tf.keras.metrics.RootMeanSquaredError()]
        )
        self.model.fit([X_voxel, X_rdkit, X_smiles], y, callbacks=callbacks, **fit_params)
        return self
    
    def predict(self, X, **predict_params):
        X_voxel, X_rdkit, X_smiles = X
        return self.model.predict([X_voxel, X_rdkit, X_smiles], **predict_params)
    
    def score(self, X, y, **score_params):
        predictions = self.predict(X)
        return -np.mean((predictions - y) ** 2)
    
    def get_params(self, deep=True):
        return {
            "optimizer": self.optimizer,
            "dropout_rate": self.dropout_rate,
            "learning_rate": self.learning_rate,
            "build_fn": self.build_fn,
            "voxel_input_shape": self.voxel_input_shape,
            "rdkit_input_shape": self.rdkit_input_shape,
            "smiles_input_shape": self.smiles_input_shape,
            "enc": self.enc,
            "max_smiles_len": self.max_smiles_len,
            **self.kwargs
        }
    
    def set_params(self, **params):
        for param, value in params.items():
            setattr(self, param, value)
        return self

train = pd.read_csv("data/train.csv").drop(6341)
test = pd.read_csv("data/test.csv")

train['pIC50'] = -np.log10(train['IC50_nM']) + 9

train_voxel_data = np.expand_dims(np.load('data/train_voxel.npy'), axis=-1)
test_voxel_data = np.expand_dims(np.load('data/test_voxel.npy'), axis=-1)

def calculate_rdkit_features(smiles):
    mol = Chem.MolFromSmiles(smiles)
    features = {
        'MolWt': Descriptors.MolWt(mol),
        'LogP': Descriptors.MolLogP(mol),
        'TPSA': rdMolDescriptors.CalcTPSA(mol),
        'NumHDonors': Descriptors.NumHDonors(mol),
        'NumHAcceptors': Descriptors.NumHAcceptors(mol),
        'NumRotatableBonds': Descriptors.NumRotatableBonds(mol),
        'FractionCSP3': rdMolDescriptors.CalcFractionCSP3(mol),
        'MinPartialCharge': Descriptors.MinPartialCharge(mol),
        'MaxPartialCharge': Descriptors.MaxPartialCharge(mol),
        'NumValenceElectrons': Descriptors.NumValenceElectrons(mol),
        'BertzCT': Descriptors.BertzCT(mol),
        'HallKierAlpha': Descriptors.HallKierAlpha(mol),
        'BalabanJ': Descriptors.BalabanJ(mol),
        'QED': QED.qed(mol)
    }
    return list(features.values())

train_rdkit_features = np.array([calculate_rdkit_features(s) for s in train['Smiles']])
test_rdkit_features = np.array([calculate_rdkit_features(s) for s in test['Smiles']])

scaler_rdkit = MinMaxScaler()
train_rdkit_features = scaler_rdkit.fit_transform(train_rdkit_features)
test_rdkit_features = scaler_rdkit.transform(test_rdkit_features)

max_smiles_len = max(train['Smiles'].apply(len).max(), test['Smiles'].apply(len).max())
enc = {'l': 1, 'y': 2, '@': 3, '3': 4, 'H': 5, 'S': 6, 'F': 7, 'C': 8, 'r': 9, 's': 10, '/': 11, 'c': 12, 'o': 13,
       '+': 14, 'I': 15, '5': 16, '(': 17, '2': 18, ')': 19, '9': 20, 'i': 21, '#': 22, '6': 23, '8': 24, '4': 25,
       '=': 26, '1': 27, 'O': 28, '[': 29, 'D': 30, 'B': 31, ']': 32, 'N': 33, '7': 34, 'n': 35, '-': 36}

def smiles_encoding(smiles, enc, max_len=max_smiles_len):
    encoded = [enc.get(char, 0) for char in smiles]
    if len(encoded) < max_len:
        encoded += [0] * (max_len - len(encoded))
    return np.array(encoded[:max_len])

train['SMILES_Encoded'] = train['Smiles'].apply(smiles_encoding, enc=enc)
test['SMILES_Encoded'] = test['Smiles'].apply(smiles_encoding, enc=enc)

train_smiles_encoded = np.stack(train['SMILES_Encoded'].values)
test_smiles_encoded = np.stack(test['SMILES_Encoded'].values)

scaler_smiles = MinMaxScaler()
train_smiles_encoded = scaler_smiles.fit_transform(train_smiles_encoded)
test_smiles_encoded = scaler_smiles.transform(test_smiles_encoded)

voxel_input_shape = train_voxel_data.shape[1:]
rdkit_input_shape = (train_rdkit_features.shape[1],)
smiles_input_shape = (max_smiles_len,)

def cnn_model(voxel_input_shape, rdkit_input_shape, smiles_input_shape, enc, max_smiles_len):
    l2_reg = regularizers.l2(0.0001)

    voxel_input = Input(shape=voxel_input_shape, name="voxel_input")
    x = layers.Conv3D(32, (3, 3, 3), padding='same', kernel_regularizer=l2_reg)(voxel_input)
    x = layers.ReLU()(x)
    x = layers.AveragePooling3D((2, 2, 2))(x)
    x = layers.Conv3D(64, (3, 3, 3), padding='same', kernel_regularizer=l2_reg)(x)
    x = layers.ReLU()(x)
    x = layers.AveragePooling3D((2, 2, 2))(x)
    x = layers.Conv3D(128, (3, 3, 3), padding='same', kernel_regularizer=l2_reg)(x)
    x = layers.ReLU()(x)
    x = layers.GlobalAveragePooling3D()(x)

    rdkit_input = Input(shape=rdkit_input_shape, name="rdkit_input")
    y = layers.Dense(64, activation='relu', kernel_regularizer=l2_reg)(rdkit_input)

    smiles_input = Input(shape=smiles_input_shape, name="smiles_input")
    z = layers.Embedding(input_dim=len(enc) + 1, output_dim=64)(smiles_input)
    z = layers.LSTM(64, return_sequences=False, kernel_regularizer=l2_reg)(z)

    combined = layers.concatenate([x, y, z])

    output = layers.Dense(128, activation='relu', kernel_regularizer=l2_reg)(combined)
    output = layers.Dropout(0.15)(output)
    output = layers.Dense(64, activation='relu', kernel_regularizer=l2_reg)(output)
    output = layers.Dropout(0.15)(output)
    output = layers.Dense(1, name='regression_output')(output)

    model = Model(inputs=[voxel_input, rdkit_input, smiles_input], outputs=output)

    optimizer_instance = tf.keras.optimizers.AdamW(learning_rate=0.001)
    model.compile(optimizer=optimizer_instance, loss='mean_squared_error', metrics=[tf.keras.metrics.RootMeanSquaredError()])
    
    return model

reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.7, patience=10, min_lr=1e-6, verbose=1)
checkpoint = ModelCheckpoint('best_model.keras', monitor='val_loss', save_best_only=True, mode='min', verbose=1)
def get_early_stopping_callback():
    return EarlyStopping(monitor='val_loss', patience=20, restore_best_weights=True)

y_train = train['pIC50'].values

callbacks = [checkpoint, reduce_lr, get_early_stopping_callback()]

with strategy.scope():
    model = cnn_model(voxel_input_shape, rdkit_input_shape, smiles_input_shape, enc, max_smiles_len)


    model.fit(
        [train_voxel_data, train_rdkit_features, train_smiles_encoded],
        y_train,
        epochs=1000,
        batch_size=32,
        validation_split=0.2,
        callbacks=callbacks
    )

final_score = model.evaluate([train_voxel_data, train_rdkit_features, train_smiles_encoded], y_train, verbose=0)
print("Final Validation RMSE:", final_score[1])

test_preds = model.predict([test_voxel_data, test_rdkit_features, test_smiles_encoded], batch_size=32)
ic50_predictions = 10 ** (9 - test_preds.flatten())
submission = pd.DataFrame({'ID': test['ID'], 'IC50_nM': ic50_predictions})
submission.to_csv('submission_3dcnn.csv', index=False)