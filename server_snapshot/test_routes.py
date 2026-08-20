
import sys
sys.path.insert(0, ".")
try:
    from main import app
    print("Routes:", [r.path for r in app.routes])
except Exception as e:
    print("Error:", e)
    import traceback
    traceback.print_exc()
