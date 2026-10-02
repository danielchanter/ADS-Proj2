import pandas as pd
import numpy as np

from pathlib import Path
from sklearn.model_selection import KFold, train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error

# definitions
def evalmodel(model, xtrain, ytrain, xtest, ytest):
    model.fit(xtrain, ytrain)
    pred = model.predict(xtest)

    mae = mean_absolute_error(ytest, pred)
    rmse = np.sqrt(mean_squared_error(ytest, pred))
    r2 = r2_score(ytest, pred)

    print("MAE:", mae)
    print("RMSE:", rmse)
    print("R²:", r2)

    return pred, mae, rmse, r2  

# loading data
base = Path(__file__).resolve().parent.parent
data = pd.read_csv(base / "modelling" / "finalproperty.csv")


# features
id_cols = ["listing_id", "sa2_code_2021", "sa3_code_2021", "sa4_code_2021", "gccsa_code_2021"]
cat_cols = ["suburb", "postcode", "property_type", "listed_month", "available_month", "sa2_name_2021", "sa3_name_2021", "sa4_name_2021", "gccsa_name_2021"]
internal = ["bedrooms", "bathrooms", "carspaces", "property_type", "carspaces_missing",	"bedrooms_missing",	"bathrooms_missing", "listed_month", "available_month"]

# target
target = "weekly_rent"

num_cols = [
    col for col in data.columns
    if col not in id_cols + cat_cols + [target]
]

external = [
    col for col in data.columns
    if col not in id_cols + internal + [target]
]

# modeling 
X_internal = data[internal]
X_external = data[external]
X_combined = data[internal + external]
y = data[target]

# linear model on the internal factors

internal_numeric = [
    col for col in internal
    if col in num_cols
]

internal_categorical = [
    col for col in internal
    if col in cat_cols
]

internal_preprocessor = ColumnTransformer([
    (
        "num",
        Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler())
        ]),
        internal_numeric
    ),
    (
        "cat",
        Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore"))
        ]),
        internal_categorical
    )
])

internal_model = Pipeline([
    ("preprocessor", internal_preprocessor),
    ("regressor", LinearRegression())
])

# random forest on external
external_numeric = [
    col for col in external
    if col in num_cols
]

external_categorical = [
    col for col in external
    if col in cat_cols
]

external_preprocessor = ColumnTransformer([
    ("num", Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler())
    ]), external_numeric),

    ("cat", Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore"))
    ]), external_categorical)
])

external_model = Pipeline([
    ("preprocessor", external_preprocessor),
    ("regressor", RandomForestRegressor(
        n_estimators=300,
        random_state=67,
        n_jobs=-1
    ))
])

# Gradient Boost Modelling on combined features aiming to look at interaction
combined_numeric = [
    col for col in internal + external
    if col in num_cols
]

combined_categorical = [
    col for col in internal + external
    if col in cat_cols
]

combined_preprocessor = ColumnTransformer([
    (
        "num",
        Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
        ]),
        combined_numeric
    ),
    (
        "cat",
        Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore"))
        ]),
        combined_categorical
    )
])

combined_model = Pipeline([
    ("preprocessor", combined_preprocessor),
    ("regressor", GradientBoostingRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_leaf_nodes=15,
        max_depth=5,
        random_state=67
    ))
])

# test/train split 
X_int_train, X_int_test, X_ext_train, X_ext_test, X_comb_train, X_comb_test, y_train, y_test = train_test_split(
    X_internal,
    X_external,
    X_combined,
    y,
    test_size=0.2,
    random_state=67
)


kf = KFold(
    n_splits=5,
    shuffle=True,
    random_state=67
)

oof_internal = np.zeros(len(X_int_train))
oof_external = np.zeros(len(X_ext_train))
oof_combined = np.zeros(len(X_comb_train))


for train_idx, val_idx in kf.split(X_int_train):

    # Split internal data
    X_int_fold_train = X_int_train.iloc[train_idx]
    X_int_fold_val = X_int_train.iloc[val_idx]

    # Split external data
    X_ext_fold_train = X_ext_train.iloc[train_idx]
    X_ext_fold_val = X_ext_train.iloc[val_idx]

    # Split combined data
    X_comb_fold_train = X_comb_train.iloc[train_idx]
    X_comb_fold_val = X_comb_train.iloc[val_idx]

    y_fold_train = y_train.iloc[train_idx]

    # train internal model
    internal_model.fit(
        X_int_fold_train,
        y_fold_train
    )

    # train external model
    external_model.fit(
        X_ext_fold_train,
        y_fold_train
    )

    # train combined
    combined_model.fit(
    X_comb_fold_train,
    y_fold_train
    )

    # validation fold
    oof_internal[val_idx] = internal_model.predict(
        X_int_fold_val
    )

    oof_external[val_idx] = external_model.predict(
        X_ext_fold_val
    )

    oof_combined[val_idx] = combined_model.predict(
    X_comb_fold_val
    )


meta_X = np.column_stack([
    oof_internal,
    oof_external,
    oof_combined
])

meta_model = LinearRegression()


# train meta-model
meta_model.fit(meta_X, y_train)


# evaluation
print("\nInternal model")

internal_pred, internal_mae, internal_rmse, internal_r2 = evalmodel(
    internal_model,
    X_int_train,
    y_train,
    X_int_test,
    y_test
)

print("\nExternal model")

external_pred, external_mae, external_rmse, external_r2 = evalmodel(
    external_model,
    X_ext_train,
    y_train,
    X_ext_test,
    y_test
)

print ("\nCombined model")
comb_pred, comb_mae, comb_rmse, comb_r2 = evalmodel(
    combined_model,
    X_comb_train,
    y_train,
    X_comb_test,
    y_test
)

test_meta_X = np.column_stack([
    internal_pred,
    external_pred,
    comb_pred,

])

final_pred = meta_model.predict(test_meta_X)

final_mae = mean_absolute_error(
    y_test,
    final_pred
)

final_rmse = np.sqrt(
    mean_squared_error(y_test, final_pred)
)

final_r2 = r2_score(
    y_test,
    final_pred
)

print("\nStacked Ensemble")
print("MAE:", final_mae)
print("RMSE:", final_rmse)
print("R²:", final_r2)

print("\nMeta-model coefficients")
print("Internal:", meta_model.coef_[0])
print("External:", meta_model.coef_[1])
print("Combined:", meta_model.coef_[2])
print("Intercept:", meta_model.intercept_)