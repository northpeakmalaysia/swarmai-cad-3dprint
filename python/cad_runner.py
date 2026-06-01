"""
cad_runner.py — JSON dispatch entry for the SwarmAI 3D-print plugin.

Protocol (process-isolated, stdin/stdout):
  IN : a single JSON object on stdin -> {"action": "...", "params": {...}}
  OUT: a single JSON object on stdout -> {"ok": true, "result": {...}}
                                      or {"ok": false, "error": "...",
                                          "missing_deps": [...] }

Actions:
  - "turbine"   : build_turbine(params)
  - "primitive" : build_primitive(params)
  - "design"    : build_design(params) — general "ask anything" code → solid
                  (backend: "mesh" = cadlib/trimesh, "brep" = build123d)
  - "validate"  : validate_stl(params["path"])
  - "selftest"  : import-only dependency check

Nothing except the final JSON is written to stdout; diagnostics go to stderr.
"""
import sys
import os
import json
import traceback

REQUIRED = ["numpy", "trimesh", "manifold3d"]
OPTIONAL = ["shapely", "matplotlib"]


def _check_deps():
    missing = []
    for mod in REQUIRED:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    return missing


def main() -> int:
    try:
        raw = sys.stdin.read()
        req = json.loads(raw or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad JSON on stdin: {e}"}))
        return 1

    action = req.get("action", "")
    params = req.get("params", {}) or {}

    if action == "selftest":
        missing = _check_deps()
        print(json.dumps({
            "ok": len(missing) == 0,
            "result": {"python": sys.version.split()[0],
                       "required_ok": len(missing) == 0,
                       "missing_deps": missing},
            "missing_deps": missing,
        }))
        return 0 if not missing else 2

    missing = _check_deps()
    if missing:
        pip = "pip install " + " ".join(REQUIRED + OPTIONAL)
        print(json.dumps({
            "ok": False,
            "error": f"missing Python dependencies: {', '.join(missing)}. "
                     f"Install with:  {pip}",
            "missing_deps": missing,
        }))
        return 2

    try:
        import cad_engine as ce
        if action == "turbine":
            result = ce.build_turbine(params)
        elif action == "primitive":
            result = ce.build_primitive(params)
        elif action == "design":
            result = ce.build_design(params)
            # build_design returns an {ok:False,...} envelope for the
            # brep-backend-missing case — pass it through unwrapped.
            if isinstance(result, dict) and result.get("ok") is False:
                print(json.dumps(result))
                return 2
        elif action == "validate":
            result = ce.validate_stl(params["path"])
        else:
            print(json.dumps({"ok": False, "error": f"unknown action '{action}'"}))
            return 1
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    # ensure local imports (cad_engine) resolve regardless of cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
