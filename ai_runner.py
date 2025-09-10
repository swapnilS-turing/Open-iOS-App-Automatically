#!/usr/bin/env python3
"""
ai_runner.py

Run:
  python3 ai_runner.py "open maps for driving from San Francisco to Los Angeles"

Features:
  - Reads final_toolset.json (list or single object).
  - Extracts source/destination/transport deterministically from NL.
  - Adds extra extraction for FaceTime, mailto, settings, calendar, streetview, spotify, things, whatsapp, uber.
  - Sends utterance + detected slots + tool schemas to OpenAI.
  - Validates model output against the JSON Schema in the tool.
  - Calls: python3 launch_app.py <module> <function> [args...]

Key handling:
  Set the key via environment only:
    1) OPENAI_API_KEY
"""

import json
import os
import re
import sys
import time
import traceback
import subprocess
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

# ----------------- Config -----------------
DEFAULT_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]
TOOLSET_FILENAME = "final_toolset.json"
LAUNCHER = "launch_app.py"
OPENAI_TIMEOUT = 30  # seconds

# ----------------- Key loading -----------------
def load_api_key() -> Optional[str]:
    value = os.getenv("OPENAI_API_KEY")
    if value and value.strip():
        return value.strip()
    return None

def get_openai_client():
    try:
        from openai import OpenAI
    except Exception:
        print("❌ Missing 'openai' package. Run: pip install openai")
        raise SystemExit(1)

    key = load_api_key()
    if not key:
        print(
            "❌ No OpenAI API key found. Set it in your environment and retry, e.g.:\n\n"
            "            export OPENAI_API_KEY=\"your-key\""
        )
        raise SystemExit(1)

    return OpenAI(api_key=key)

# ----------------- File helpers -----------------

def load_toolset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"❌ Could not find {path}")
        raise SystemExit(1)
    with path.open("r") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]

# ----------------- Deterministic NL slot extraction -----------------
TRANSPORT_SYNONYMS = {
    "driving": "d", "drive": "d", "by car": "d", "car": "d",
    "walking": "w", "walk": "w", "on foot": "w",
    "transit": "r", "public transit": "r", "bus": "r", "train": "r", "metro": "r", "subway": "r",
    "cycling": "c", "bike": "c", "biking": "c"
}

def _clean(s: str) -> str:
    return s.strip().strip('\'\"').strip().rstrip(" .,!?:;")

def _find_transport(text: str) -> Optional[str]:
    low = text.lower()
    for k, v in TRANSPORT_SYNONYMS.items():
        if k in low:
            return v
    return None

def extract_slots(utterance: str) -> Dict[str, str]:
    text = utterance.strip()
    slots: Dict[str, str] = {}

    # transport first
    t = _find_transport(text)
    if t:
        slots["transport"] = t

    # 1) "from X to Y"
    m = re.search(r"\bfrom\s+(?P<src>.+?)\s+(?:to|->)\s+(?P<dst>.+)$", text, flags=re.IGNORECASE)
    if m:
        slots["source"] = _clean(m.group("src"))
        slots["destination"] = _clean(m.group("dst"))
        return slots

    # 2) "to Y from X"
    m = re.search(r"\bto\s+(?P<dst>.+?)\s+(?:from|<-)\s+(?P<src>.+)$", text, flags=re.IGNORECASE)
    if m:
        slots["source"] = _clean(m.group("src"))
        slots["destination"] = _clean(m.group("dst"))
        return slots

    # 3) "X to Y"
    m = re.search(r"\b(?P<src>[^,]+?)\s+(?:to|->)\s+(?P<dst>[^,]+)$", text, flags=re.IGNORECASE)
    if m:
        slots["source"] = _clean(m.group("src"))
        slots["destination"] = _clean(m.group("dst"))
        return slots

    slots["query"] = _clean(text)
    return slots

APP_HINTS = {
    "apple_maps": ["apple maps", "maps"],
    "google_maps": ["google maps", "street view", "streetview"],
    "facetime": ["facetime", "video call"],
    "mailto": ["email", "mailto"],
    "settings": ["settings", "wifi", "bluetooth", "cellular"],
    "calendar": ["calendar", "calshow"],
    "spotify": ["spotify"],
    "things": ["things 3", "things app", "todo", "to-do", "task"],
    "whatsapp": ["whatsapp"],
    "uber": ["uber"]
}

def detect_preferred_tool(utterance: str) -> Optional[str]:
    low = utterance.lower()
    for tool, keys in APP_HINTS.items():
        for k in keys:
            if k in low:
                return tool
    return None

def extract_extra_slots(utterance: str) -> dict:
    low = utterance.lower().strip()
    slots = {}

    # FaceTime
    if "facetime" in low or "video call" in low:
        m_phone = re.search(r"(\+?\d[\d\s\-().]{6,}\d)", utterance)
        if m_phone:
            slots["phone_or_email"] = m_phone.group(1).strip()
        else:
            m_mail = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", utterance)
            if m_mail:
                slots["phone_or_email"] = m_mail.group(1)

    # Mailto
    if "mailto" in low or "email" in low:
        to = re.search(r"(?:email|mailto)\s+(?:to\s+)?(?P<to>[^\s,;]+)", low)
        if to:
            slots["recipient"] = to.group("to")
        sub = re.search(r"subject\s*[:=]\s*(?P<sub>.+?)(?:\s+body[:=]|$)", utterance, re.IGNORECASE)
        if sub:
            slots["subject"] = sub.group("sub").strip()
        body = re.search(r"body\s*[:=]\s*(?P<body>.+)$", utterance, re.IGNORECASE)
        if body:
            slots["body"] = body.group("body").strip()

    # Settings
    if "settings" in low:
        if "wifi" in low: slots["root"] = "WIFI"
        elif "bluetooth" in low: slots["root"] = "Bluetooth"
        elif "cellular" in low: slots["root"] = "MOBILE_DATA_SETTINGS_ID"

    # Calendar
    m_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", low)
    if m_date:
        slots["date_yyyy_mm_dd"] = m_date.group(1)

    # Google Maps Street View
    if "street view" in low or "streetview" in low or "google maps" in low:
        m_center = re.search(r"\b(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\b", utterance)
        if m_center:
            slots["center_lat"] = m_center.group(1)
            slots["center_lng"] = m_center.group(2)

    # Spotify
    if "spotify" in low:
        m = re.search(r"spotify\s+(?:search\s+)?(?P<q>.+)$", utterance, re.IGNORECASE)
        if m:
            slots["query"] = m.group("q").strip()

    # Things
    if "things" in low or "to-do" in low or "todo" in low or "task" in low:
        m = re.search(r"(?:things|todo|to-do|task)\s+(?:add\s+)?(?P<title>[^|]+)", utterance, re.IGNORECASE)
        if m:
            slots["title"] = m.group("title").strip()

    # WhatsApp
    if "whatsapp" in low:
        m_phone = re.search(r"(\+?\d[\d\s\-().]{6,}\d)", utterance)
        if m_phone: slots["phone"] = m_phone.group(1).strip()
        m_text = re.search(r"text\s*[:=]\s*(?P<txt>.+)$", utterance, re.IGNORECASE)
        if m_text: slots["text"] = m_text.group("txt").strip()

    # Uber
    if "uber" in low:
        mp = re.search(r"pickup\s*[:=]\s*(?P<plat>-?\d{1,3}\.\d+)\s*,\s*(?P<plng>-?\d{1,3}\.\d+)", utterance, re.IGNORECASE)
        if mp:
            slots["pickup_lat"] = mp.group("plat")
            slots["pickup_lng"] = mp.group("plng")
        md = re.search(r"dropoff\s*[:=]\s*(?P<dlat>-?\d{1,3}\.\d+)\s*,\s*(?P<dlng>-?\d{1,3}\.\d+)", utterance, re.IGNORECASE)
        if md:
            slots["dropoff_lat"] = md.group("dlat")
            slots["dropoff_lng"] = md.group("dlng")

    hint = detect_preferred_tool(utterance)
    if hint:
        slots["_preferred_tool"] = hint

    return slots

# ----------------- Schema validation -----------------
def _coerce_type(py_type: str, val: Any) -> Any:
    if py_type == "string":
        return str(val)
    if py_type == "number":
        return float(val)
    if py_type == "integer":
        return int(val)
    if py_type == "boolean":
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
        raise ValueError(f"Cannot coerce '{val}' to boolean")
    if py_type == "array":
        if isinstance(val, list):
            return val
        raise ValueError(f"Expected array, got {type(val).__name__}")
    if py_type == "object":
        if isinstance(val, dict):
            return val
        raise ValueError(f"Expected object, got {type(val).__name__}")
    return val

def _friendly_enum_map(name: str, val: Any, enum_vals: List[Any]) -> Any:
    if name == "transport" and isinstance(val, str):
        low = val.strip().lower()
        for k, v in TRANSPORT_SYNONYMS.items():
            if k == low:
                return v
    return val

def validate_args_against_schema(tool: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    decl = tool.get("declaration", {})
    schema = decl.get("parameters", {}) or {}
    props = schema.get("properties", {}) or {}
    required = schema.get("required", []) or []
    addl_ok = schema.get("additionalProperties", True)

    missing = [r for r in required if r not in args]
    if missing:
        raise ValueError(f"Missing required arguments: {missing}")

    out: Dict[str, Any] = {}
    for name, spec in props.items():
        if name not in args:
            continue
        val = args[name]
        typ = spec.get("type")
        if typ:
            val = _coerce_type(typ, val)
        enum_vals = spec.get("enum")
        if enum_vals:
            v2 = _friendly_enum_map(name, val, enum_vals)
            if v2 not in enum_vals:
                raise ValueError(f"Parameter '{name}' must be one of {enum_vals}, got '{val}'")
            val = v2
        out[name] = val

    if not addl_ok:
        out = {k: v for k, v in out.items() if k in props}

    return out

# ----------------- OpenAI routing -----------------
def call_openai_route(client, model: str, utterance: str, tools_summary: List[Dict[str, Any]], detected_slots: Dict[str, str]) -> Dict[str, Any]:
    system_msg = (
        "You are a router. Given a user utterance and a list of tools with JSON Schemas, "
        "choose the single best tool and output ONLY valid JSON with 'tool_name', 'arguments', and optional 'argv'. "
        "Respect enums and required fields. Use the provided 'detected_slots' if they match the schema."
    )
    guidance = (
        "If the utterance contains 'from X to Y', map X -> 'source' and Y -> 'destination'. "
        "If it mentions driving/walking/public transit/cycling, map to 'transport' as 'd'/'w'/'r'/'c'. "
        "Do not include explanations."
    )

    user_payload = {
        "utterance": utterance,
        "detected_slots": {**detected_slots, **extract_extra_slots(utterance)},
        "tools": tools_summary,
        "instructions": guidance,
        "output_format": {
            "tool_name": "string",
            "arguments": "object (keys must match the tool's parameters schema)",
            "argv": "array of strings in call order (optional)"
        }
    }

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
        ],
        timeout=OPENAI_TIMEOUT
    )

    raw = (resp.choices[0].message.content or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        raise RuntimeError(f"Model returned non-JSON output:\n{raw}")

# ----------------- Function signature helpers -----------------
def get_function_param_order(module_path: Path, func_name: str) -> List[str]:
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, func_name)
    from inspect import signature
    return list(signature(fn).parameters.keys())

def build_argv(tool: Dict[str, Any], args: Dict[str, Any]) -> List[str]:
    exec_ = tool.get("execution", {})
    module = exec_.get("module")
    func = exec_.get("function")
    module_path = Path(__file__).with_name(module)

    try:
        param_order = get_function_param_order(module_path, func)
        ordered = [str(args[p]) for p in param_order if p in args]
        if not ordered:
            for cand in ("query", "destination", "source"):
                if cand in args:
                    ordered = [str(args[cand])]
                    break
        return ordered
    except Exception:
        fallback_keys = ["source", "destination", "transport", "query"]
        ordered = [str(args[k]) for k in fallback_keys if k in args]
        if not ordered and args:
            ordered = [str(v) for _, v in sorted(args.items())]
        return ordered

# ----------------- Main -----------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 ai_runner.py \"<natural language command>\"")
        raise SystemExit(1)

    utterance = " ".join(sys.argv[1:]).strip()
    base = Path(__file__).parent
    launcher = base / LAUNCHER

    # New structure: each tool's final_toolset.json lives under agentic_ios_tools/<tool>/final_toolset.json
    tools_dir = base / "agentic_ios_tools"
    tools: List[Dict[str, Any]] = []
    if tools_dir.is_dir():
        json_files = sorted(tools_dir.rglob("final_toolset.json"))
        if not json_files:
            print(f"❌ No final_toolset.json files found under {tools_dir}")
            raise SystemExit(1)
        print(f"🔧 Reading toolsets from {tools_dir} ({len(json_files)} files)")
        for p in json_files:
            try:
                data = load_toolset(p)
                if isinstance(data, list):
                    tools.extend(data)
                else:
                    tools.append(data)
            except Exception as e:
                print(f"⚠️ Skipping {p}: {e}")
    else:
        # Fallback to legacy single-file location next to ai_runner.py
        toolset_path = base / TOOLSET_FILENAME
        print(f"🔧 Reading toolset: {toolset_path}")
        tools = load_toolset(toolset_path)

    tools = [t for t in tools if t.get("execution", {}).get("module") and t.get("execution", {}).get("function")]
    if not tools:
        print("❌ No tools with execution.module/function found in final_toolset.json")
        raise SystemExit(2)

    tool_summaries = []
    for t in tools:
        decl = t.get("declaration", {})
        params = decl.get("parameters", {})
        exec_ = t.get("execution", {})
        tool_summaries.append({
            "name": decl.get("name"),
            "description": decl.get("description"),
            "parameters": params,
            "execution": {"module": exec_.get("module"), "function": exec_.get("function")}
        })

    detected = extract_slots(utterance)
    print(f"🧭 Detected slots: {json.dumps(detected, ensure_ascii=False)}")

    print(f"🧠 Calling OpenAI with models: {', '.join(DEFAULT_MODELS)}")
    client = get_openai_client()

    choice = None
    last_err = None
    for m in DEFAULT_MODELS:
        try:
            t0 = time.time()
            choice = call_openai_route(client, m, utterance, tool_summaries, detected)
            dt = time.time() - t0
            print(f"✅ Model '{m}' responded in {dt:.1f}s")
            break
        except Exception as e:
            last_err = e
            print(f"⚠️  {e}")

    if choice is None:
        print("❌ All model attempts failed.")
        if last_err:
            print(last_err)
        raise SystemExit(3)

    tool_name = choice.get("tool_name")
    model_args = choice.get("arguments", {}) or {}
    argv_from_model = choice.get("argv") or []

    merged_args = {**detected, **extract_extra_slots(utterance), **model_args}

    tool = next((t for t in tools if t.get("declaration", {}).get("name") == tool_name), None)
    if not tool:
        print(f"❌ Model chose unknown tool '{tool_name}'. Available:", [t.get('declaration',{}).get('name') for t in tools])
        raise SystemExit(4)

    try:
        validated = validate_args_against_schema(tool, merged_args)
    except Exception as e:
        print("❌ Argument validation failed.")
        print("Reason:", e)
        print("Merged arguments:", json.dumps(merged_args, ensure_ascii=False))
        raise SystemExit(5)

    argv = [str(x) for x in argv_from_model] if argv_from_model else build_argv(tool, validated)

    exec_ = tool["execution"]
    module = exec_["module"]
    function = exec_["function"]

    if not launcher.exists():
        print(f"❌ Could not find {LAUNCHER} next to ai_runner.py")
        raise SystemExit(6)

    cmd = ["python3", str(launcher), module, function, *argv]
    print("🔎 Decision:")
    print("  • Tool      :", tool_name)
    print("  • Arguments :", json.dumps(validated, ensure_ascii=False))
    print("  • Command   :", " ".join(f'"{c}"' if " " in c else c for c in cmd))

    print("🚀 Launching…")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print("❌ Launch failed with non-zero exit status.")
        print(e)
        raise SystemExit(e.returncode)
    except Exception:
        print("❌ Unexpected error while launching.")
        traceback.print_exc()
        raise SystemExit(7)

if __name__ == "__main__":
    main()
