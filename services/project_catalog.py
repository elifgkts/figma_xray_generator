PROJECT_GROUPS = {
    "bip": [
        "TET", "TF", "BIPC", "EKO", "BVSAD", "BVB", "ATAK",
        "BS343690", "ALS", "APEX", "WEBE2E", "WT", "BU471990", "DSSBMD",
    ],
    "fizy": [
        "FZ471074",
    ],
    "gameplus": [
        "DSSDAGAME",
    ],
    "lifebox": [
        "LB", "DSSDALB",
    ],
    "sardis": [
        "SARDIS", "SUBSRDS", "DSSDASR", "DSSDASBSR",
    ],
    "tvplus": [
        "DSSDATBB", "DSSDATR", "DSSDATRT", "DSSDATVB", "DSSTV",
        "MRB", "TBB", "TMA", "TR", "TRT",
    ],
}

PROJECT_LABELS = {
    key: f"{key.upper()} ({', '.join(values)})"
    for key, values in PROJECT_GROUPS.items()
}


def all_project_keys():
    keys = []
    for values in PROJECT_GROUPS.values():
        keys.extend(values)
    return keys
