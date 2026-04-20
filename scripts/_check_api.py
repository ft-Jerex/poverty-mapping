"""Quick API check."""
import urllib.request
import json

r = urllib.request.urlopen("http://localhost:8000/api/predictions")
d = json.load(r)
m = d.get("models", {})

for key in ["catboost", "rf", "cnn"]:
    fc = m.get(key)
    if fc is None:
        print(f"{key}: None")
        continue
    feats = fc.get("features", [])
    if not feats:
        print(f"{key}: 0 features")
        continue
    pvs = [f["properties"]["poverty_pct"] for f in feats]
    print(f"{key}: {len(feats)} features, range [{min(pvs):.1f}, {max(pvs):.1f}], mean {sum(pvs)/len(pvs):.1f}")

bnd = d.get("boundary", {})
bnd_feats = bnd.get("features", []) if bnd else []
print(f"Boundary: {len(bnd_feats)} features")
labels = d.get("barangayLabels", {})
lbl_feats = labels.get("features", []) if labels else []
print(f"Labels: {len(lbl_feats)} features")

if d.get("error"):
    print(f"ERROR: {d['error']}")
