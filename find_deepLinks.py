#!/usr/bin/env python3
import os, sys, json, glob, plistlib, subprocess, shlex

def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

def get_booted_udid():
    rc, out, err = run(["xcrun", "simctl", "list", "devices", "booted", "--json"])
    if rc != 0:
        print(f"# ERROR: simctl list devices failed: {err}", file=sys.stderr)
        return None
    data = json.loads(out or "{}")
    devs = data.get("devices", {})
    # devices is a dict keyed by runtime; collect any booted device
    for _, lst in devs.items():
        for d in lst:
            if d.get("state") == "Booted":
                return d.get("udid")
    return None

def list_app_paths_by_scanning(udid):
    base = os.path.expanduser(f"~/Library/Developer/CoreSimulator/Devices/{udid}/data/Containers/Bundle/Application")
    results = []
    if not os.path.isdir(base):
        return results
    for guid in os.listdir(base):
        gpath = os.path.join(base, guid)
        if not os.path.isdir(gpath):
            continue
        # inside each GUID: one or more .app bundles
        for app_path in glob.glob(os.path.join(gpath, "*.app")):
            results.append(app_path)
        # occasionally nested folder then .app
        for app_path in glob.glob(os.path.join(gpath, "*", "*.app")):
            results.append(app_path)
    return sorted(set(results))

def maybe_listapps(udid):
    # Prefer simctl listapps if available (Xcode 15+), else fallback to scanning
    rc, out, err = run(["xcrun", "simctl", "listapps", udid, "--json"])
    if rc != 0 or not out:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    # structure may vary; try common fields
    apps = []
    for bundle_id, info in data.get("apps", {}).items():
        # try to find path to the .app bundle
        # Some versions expose 'path' or 'app_path'
        app_path = info.get("path") or info.get("app_path") or info.get("bundlePath")
        if app_path and app_path.endswith(".app") and os.path.exists(app_path):
            apps.append(app_path)
    return sorted(set(apps)) if apps else None

def read_info(app_path):
    info_plist = os.path.join(app_path, "Info.plist")
    if not os.path.isfile(info_plist):
        return {}
    with open(info_plist, "rb") as f:
        try:
            return plistlib.load(f)
        except Exception:
            return {}

def get_entitlements(app_path):
    # codesign -d writes to STDERR; ask it to dump entitlements as XML
    rc, out, err = run(["/usr/bin/codesign", "-d", "--entitlements", ":-", app_path])
    blob = err or out or ""
    blob = blob.strip()
    if not blob:
        return {}
    # codesign often prefixes with "Executable=" and other lines; extract the XML/plist only
    # Try to find the first `<plist` and the last `</plist>`
    start = blob.find("<plist")
    end = blob.rfind("</plist>")
    if start != -1 and end != -1:
        xml = blob[start:end+8]
        try:
            return plistlib.loads(xml.encode("utf-8"))
        except Exception:
            pass
    # Fallback: try reading as binary plist (rare in this path)
    try:
        return plistlib.loads(blob.encode("utf-8"))
    except Exception:
        return {}

def extract_schemes(info):
    schemes = []
    for item in info.get("CFBundleURLTypes", []) or []:
        for s in item.get("CFBundleURLSchemes", []) or []:
            if s:
                schemes.append(str(s))
    return sorted(set(schemes))

def extract_universal_link_domains(ents):
    domains = []
    arr = ents.get("com.apple.developer.associated-domains", []) or []
    for entry in arr:
        # entries look like "applinks:example.com", "applinks:sub.example.com?mode=developer"
        if isinstance(entry, str) and entry.startswith("applinks:"):
            d = entry[len("applinks:"):]
            # strip optional params after '?'
            d = d.split("?")[0]
            domains.append(d)
    return sorted(set(domains))

def csv_escape(s):
    s = s or ""
    if any(c in s for c in [",", "\"", "\n"]):
        return "\"" + s.replace("\"", "\"\"") + "\""
    return s

def main():
    udid = get_booted_udid()
    if not udid:
        print("ERROR: No booted simulator found. Please boot one in Xcode and retry.", file=sys.stderr)
        sys.exit(1)

    app_paths = maybe_listapps(udid) or list_app_paths_by_scanning(udid)

    print("bundle_id,app_name,url_schemes,universal_link_domains")
    for app in app_paths:
        info = read_info(app)
        if not info:
            continue
        bundle_id = info.get("CFBundleIdentifier", "")
        app_name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or os.path.basename(app).replace(".app","")
        schemes = extract_schemes(info)
        ents = get_entitlements(app)
        domains = extract_universal_link_domains(ents)

        schemes_str = ";".join(schemes)
        domains_str = ";".join(domains)

        row = [
            csv_escape(bundle_id),
            csv_escape(app_name),
            csv_escape(schemes_str),
            csv_escape(domains_str),
        ]
        print(",".join(row))

if __name__ == "__main__":
    main()
