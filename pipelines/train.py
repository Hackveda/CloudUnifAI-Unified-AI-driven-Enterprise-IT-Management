"""Train a reproducible linear RCA ranker from manually verified JSONL incidents.

Input lines: {"events":[{...}], "root_cause":"one of configured classes"}
No synthetic labels are presented as real training evidence. Output is safe JSON.
Run: python -m pipelines.train --input data/verified_incidents.jsonl --output /tmp/rca-model.json
"""
import argparse
import json
from pathlib import Path
from app.ml.pipeline import KEYWORDS, features


def train(input_path: str, output_path: str):
    try:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression
        import numpy as np
    except ImportError as ex:
        raise SystemExit("Install optional training dependencies: pip install scikit-learn numpy") from ex
    rows = [json.loads(line) for line in Path(input_path).read_text().splitlines() if line.strip()]
    if len(rows) < 20 or len({r["root_cause"] for r in rows}) < 2:
        raise ValueError("Need >=20 adjudicated incidents across >=2 root-cause classes")
    if any(r["root_cause"] not in KEYWORDS for r in rows):
        raise ValueError("Unknown root-cause label")
    vectorizer = DictVectorizer(sparse=False)
    X = vectorizer.fit_transform([features(r["events"]) for r in rows])
    y = [r["root_cause"] for r in rows]
    # Explicitly reject missing classes; coefficient rows must match all candidate classes.
    if set(y) != set(KEYWORDS):
        raise ValueError("Training data must cover all configured root causes")
    classifier = LogisticRegression(max_iter=1500, class_weight="balanced", random_state=13)
    classifier.fit(X, y)
    coefficients = classifier.coef_
    if len(classifier.classes_) == 2:
        # Binary sklearn stores one row for second class.
        coefficients = np.vstack([-coefficients[0], coefficients[0]])
        intercepts = np.array([-classifier.intercept_[0], classifier.intercept_[0]])
    else:
        intercepts = classifier.intercept_
    weights = {}
    for i, cls in enumerate(classifier.classes_):
        weights[str(cls)] = {"_bias": float(intercepts[i])}
        weights[str(cls)].update({name: float(value) for name, value in zip(vectorizer.feature_names_, coefficients[i])})
    model = {"schema": 1, "version": "linear-v1", "training_rows": len(rows),
             "warning": "Uncalibrated ranking scores. Validate on a time-separated holdout before promotion.",
             "weights": weights}
    Path(output_path).write_text(json.dumps(model, indent=2, sort_keys=True))
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    train(args.input, args.output)
