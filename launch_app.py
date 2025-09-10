import sys
import subprocess
import importlib.util
import json
import re
from pathlib import Path

# ----------------------------
# Simulator helpers
# ----------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

def _any_booted() -> bool:
    try:
        out = _run(["xcrun", "simctl", "list", "devices", "booted"])
        return "Booted" in out.stdout
    except subprocess.CalledProcessError:
        return False

def _rank_for_iphone_name(name: str) -> tuple[int, int]:
    """
    Return a sort key (major_number, tier_rank) so higher is "newer/better".
    Examples:
      iPhone 16 Pro Max -> (16, 3)
      iPhone 16 Pro     -> (16, 2)
      iPhone 16 Plus    -> (16, 1)
      iPhone 16         -> (16, 0)
      iPhone SE (3rd generation) -> (0, -1)  # pushed to the end
    """
    tier_map = {"pro max": 3, "promax": 3, "pro": 2, "plus": 1}
    m = re.search(r"iPhone\s+(\d+)", name)
    if not m:
        # SE and other non-numbered models are treated as least-preferred
        return (0, -1)

    number = int(m.group(1))
    lowered = name.lower()
    tier = -1
    for key, val in tier_map.items():
        if key in lowered:
            tier = val
            break
    if tier == -1:
        tier = 0
    return (number, tier)

def _list_available_iphones() -> list[dict]:
    """
    Returns a list of available iPhone devices from simctl JSON:
    [{ 'name': str, 'udid': str, 'state': 'Shutdown'|'Booted', 'isAvailable': bool, 'runtime': str }]
    """
    out = _run(["xcrun", "simctl", "list", "devices", "available", "-j"])
    data = json.loads(out.stdout)
    devices = []
    for _runtime, devs in data.get("devices", {}).items():
        for d in devs:
            name = d.get("name", "")
            if not name.startswith("iPhone"):
                continue
            if not d.get("isAvailable", False):
                continue
            devices.append({
                "name": name,
                "udid": d.get("udid"),
                "state": d.get("state"),
                "runtime": _runtime,
            })
    # Sort from newest/best -> oldest using our rank
    devices.sort(key=lambda d: _rank_for_iphone_name(d["name"]), reverse=True)
    return devices

def _boot_best_available_iphone() -> str:
    """
    Boots the best available iPhone (newest first). Returns the UDID that ends up booted.
    Tries each candidate in order until one succeeds.
    """
    devices = _list_available_iphones()
    if not devices:
        raise RuntimeError("No available iPhone simulators found.")

    # If any iPhone is already booted, prefer the best booted one
    booted = [d for d in devices if d["state"] == "Booted"]
    if booted:
        # Already have a booted device; choose the best ranked among booted
        booted.sort(key=lambda d: _rank_for_iphone_name(d["name"]), reverse=True)
        return booted[0]["udid"]

    # Otherwise, try booting from newest to oldest
    last_err = None
    for d in devices:
        udid = d["udid"]
        name = d["name"]
        try:
            print(f"ℹ️  Booting {name} ({udid})...")
            # Start boot
            subprocess.run(["xcrun", "simctl", "boot", udid], check=True)
            # Ensure the Simulator app is open
            subprocess.run(["open", "-a", "Simulator"], check=True)
            # Wait until fully booted
            subprocess.run(["xcrun", "simctl", "bootstatus", udid, "-b"], check=True)
            return udid
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Could not boot {name}: {e}")
            last_err = e
            continue

    if last_err:
        raise RuntimeError("Failed to boot any iPhone simulator.") from last_err
    raise RuntimeError("Failed to boot any iPhone simulator.")

def _ensure_booted_iphone() -> None:
    """
    Ensure at least one iPhone simulator is booted. Boot the best available if none is booted.
    """
    if _any_booted():
        return
    _boot_best_available_iphone()

# ----------------------------
# Main (preserves your original flow)
# ----------------------------

def main():
    # 1. Validate args
    if len(sys.argv) < 3:
        print("❌ Error: Incorrect number of arguments.")
        print("Usage: python3 launch_app.py <file_name.py> <function_name> [args...]")
        sys.exit(1)

    file_path_str = sys.argv[1]
    function_name = sys.argv[2]
    function_args = sys.argv[3:]
    file_path = Path(file_path_str)

    # 2. Validate that the file exists
    if not file_path.is_file():
        print(f"❌ Error: File not found at '{file_path}'")
        sys.exit(1)

    try:
        # 3. Import the specified Python file as a module
        spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 4. Get the function
        target_function = getattr(module, function_name)

        # 5. Call the function to build the URL scheme
        print(f"✅ Calling '{function_name}' with arguments: {function_args}")
        url_scheme = target_function(*function_args)
        print(f"✅ Retrieved URL Scheme: '{url_scheme}'")

        # NEW: ensure a simulator is booted (picks the newest iPhone, falls back automatically)
        _ensure_booted_iphone()

        # 6. Construct and execute the simulator command (same as before)
        command = f"xcrun simctl openurl booted '{url_scheme}'"
        print(f"🚀 Executing: {command}")
        subprocess.run(command, shell=True, check=True)
        print("\n🎉 Successfully launched the app in the simulator!")

    except AttributeError:
        print(f"❌ Error: Function '{function_name}' not found in '{file_path}'.")
    except TypeError:
        print(f"❌ Error: Incorrect number of arguments for function '{function_name}'.")
        print(f"Provided {len(function_args)} arguments, but the function might require a different number.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
