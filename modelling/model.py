import pandas as pd
import tensorflow as tf
import numpy as np
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_squared_error, r2_score

# FUNCTIONS

def predict_rent(new_property, model, preprocessor, id_cols):
    new_property = new_property.drop(
        columns=id_cols,
        errors="ignore"
    )

    new_property_processed = preprocessor.transform(new_property)

    predictions = model.predict(
        new_property_processed,
        verbose=0
    ).flatten()

    return predictions






# loading data
base = Path(__file__).resolve().parent.parent
data = pd.read_csv(base / "modelling" / "finalproperty.csv")

# Neural Network Model for predicting weekly rent using TensorFlow and Keras
target = "weekly_rent"
X = data.drop(columns=[target])
y = data[target]

# defining categorical and numerical columns
name_cols = [
    "sa2_name_2021",
    "sa3_name_2021",
    "sa4_name_2021",
    "gccsa_name_2021"
]

id_cols = [
    "listing_id",
    "sa2_code_2021",
    "sa3_code_2021",
    "sa4_code_2021",
    "gccsa_code_2021"
]

cat_cols = [
    "suburb",
    "postcode",
    "property_type",
    "listed_month",
    "available_month"
]

cat_cols = cat_cols + name_cols

# remove identifiers from X
X = X.drop(columns=id_cols)

# everything else is numerical
num_cols = [
    col for col in X.columns
    if col not in cat_cols
]

# test train split (80/20)
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=67,
)

# imputation and scaling for numerical features
numeric_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler", StandardScaler())
])

# one hot encoding for categorical features
categorical_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="most_frequent")),
    ("onehot", OneHotEncoder(
        handle_unknown="ignore",
        sparse_output=False
    ))
])

# combining the two pipelines into a column transformer
preprocessor = ColumnTransformer([
    ("num", numeric_pipeline, num_cols),
    ("cat", categorical_pipeline, cat_cols)
])

# fitting the preprocessor and transforming the data
X_train_processed = preprocessor.fit_transform(X_train)
X_test_processed = preprocessor.transform(X_test)


# building the model
# base line 
baseline_pred = np.full(
    len(y_test),
    y_train.mean()
)

baseline_rmse = np.sqrt(
    mean_squared_error(y_test, baseline_pred)
)

baseline_r2 = r2_score(
    y_test,
    baseline_pred
)

print("Baseline RMSE:", baseline_rmse)
print("Baseline R²:", baseline_r2)

# actual model
model = tf.keras.Sequential([
    tf.keras.layers.Input(
        shape=(X_train_processed.shape[1],)
    ),

    tf.keras.layers.Dense(
        64,
        activation="relu",
        kernel_regularizer=tf.keras.regularizers.l2(0.001)
    ),

    tf.keras.layers.Dense(
        32,
        activation="relu",
        kernel_regularizer=tf.keras.regularizers.l2(0.001)
    ),

    tf.keras.layers.Dense(16, activation="relu"),

    tf.keras.layers.Dense(1)
])


model.compile(
    optimizer="adam",
    loss="mse",
    metrics=[
        tf.keras.metrics.RootMeanSquaredError(name="rmse")
    ]
)

early_stopping = tf.keras.callbacks.EarlyStopping(
    monitor="val_loss",
    patience=15,
    restore_best_weights=True
)

history = model.fit(
    X_train_processed,
    y_train,
    validation_split=0.2,
    epochs=300,
    batch_size=32,
    callbacks=[early_stopping],
    verbose=1
)


y_pred = model.predict(X_test_processed).flatten()

rmse = mean_squared_error(y_test, y_pred) ** 0.5
r2 = r2_score(y_test, y_pred)

# for evaluation purposes
# print("RMSE:", rmse)
# print("R²:", r2)



