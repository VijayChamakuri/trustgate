import numpy as np
import pandas as pd

from trustgate.realgl import Lookup, ece, label_ceiling


def toy():
    return pd.DataFrame(
        {
            "merchant_key": ["ASDA STORES", "ASDA STORES", "ASDA STORES", "TRAINLINE", "NEW SHOP"],
            "merchant_raw": ["ASDA STORES 1", "ASDA STORES 1", "ASDA STORES 2", "TRAINLINE", "NEW SHOP"],
            "department": ["A", "A", "B", "A", "B"],
            "service_category": ["x", "x", "y", "x", "y"],
            "gl_account": ["Catering", "Catering", "Equipment", "Travel", "Equipment"],
        }
    )


def test_label_ceiling_counts_staff_disagreement():
    df = toy()
    df.loc[1, "gl_account"] = "Equipment"  # identical input booked two ways
    # groups: (ASDA1,A,x) -> 1/2 majority share; three singleton groups -> 1.0 each
    assert abs(label_ceiling(df, ["merchant_raw", "department", "service_category"]) - (0.5 * 2 + 3) / 5) < 1e-9


def test_lookup_falls_back_to_department_for_unseen_merchants():
    df = toy()
    m = Lookup().fit(df.iloc[:4], ["department", "service_category"])
    pred = m.predict(pd.DataFrame({"merchant_key": ["NEVER SEEN"], "department": ["B"], "service_category": ["y"]}))
    assert pred[0] == "Equipment"


def test_ece_is_zero_when_perfectly_calibrated():
    conf = np.array([0.8] * 10)
    correct = np.array([1] * 8 + [0] * 2, dtype=float)
    assert ece(conf, correct) < 1e-9
