#!/usr/bin/env python3
"""
飞书妙记 API 调用脚本

自动从飞书 OpenClaw 官方插件的 token store 解密获取 user_access_token。
如果 token 缺少妙记权限，自动发起 Device Flow 并通过飞书 IM 给用户发送授权卡片，
轮询等待用户点击授权后自动继续。

用法:
  python3 minutes.py info <minute_token>
  python3 minutes.py transcript <minute_token> [--speaker] [--timestamp] [--format txt|srt]
  python3 minutes.py media <minute_token>
  python3 minutes.py statistics <minute_token> [--user-id-type open_id]
  python3 minutes.py artifacts <minute_token>
"""

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

DOMAIN = os.environ.get("FEISHU_DOMAIN", "https://open.feishu.cn").rstrip("/")
ACCOUNTS_DOMAIN = "https://accounts.feishu.cn"
APPLINK_DOMAIN = "https://applink.feishu.cn"
MINUTES_SCOPES = "minutes:minutes:readonly minutes:minutes.transcript:export"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ── token store ──

def _uat_store_dir():
    xdg = os.environ.get("XDG_DATA_HOME", os.path.join(Path.home(), ".local", "share"))
    return os.path.join(xdg, "openclaw-feishu-uat")


def _decrypt_with_node(enc_file, master_key_path):
    script = (
        'const fs=require("fs"),crypto=require("crypto");'
        f'const key=fs.readFileSync({json.dumps(master_key_path)});'
        f'const data=fs.readFileSync({json.dumps(enc_file)});'
        'const iv=data.subarray(0,12),tag=data.subarray(12,28),enc=data.subarray(28);'
        'const d=crypto.createDecipheriv("aes-256-gcm",key,iv);d.setAuthTag(tag);'
        'console.log(Buffer.concat([d.update(enc),d.final()]).toString("utf8"));'
    )
    try:
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


def _encrypt_with_node(plaintext_json, master_key_path, output_path):
    script = (
        'const fs=require("fs"),crypto=require("crypto");'
        f'const key=fs.readFileSync({json.dumps(master_key_path)});'
        f'const plain={json.dumps(plaintext_json)};'
        'const iv=crypto.randomBytes(12);'
        'const c=crypto.createCipheriv("aes-256-gcm",key,iv);'
        'const enc=Buffer.concat([c.update(plain,"utf8"),c.final()]);'
        f'fs.writeFileSync({json.dumps(output_path)},Buffer.concat([iv,c.getAuthTag(),enc]),{{mode:0o600}});'
    )
    try:
        return subprocess.run(["node", "-e", script], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def read_plugin_store(enc_filename=None):
    """返回 (token_data, enc_file_path) 或 (None, None)
    如果指定 enc_filename 则只读该文件。"""
    store_dir = _uat_store_dir()
    if not os.path.isdir(store_dir):
        return None, None
    master_key_path = os.path.join(store_dir, "master.key")
    if not os.path.isfile(master_key_path):
        return None, None

    if enc_filename:
        enc_file = os.path.join(store_dir, enc_filename)
        if not enc_filename.endswith(".enc"):
            enc_file += ".enc"
        if not os.path.isfile(enc_file):
            return None, None
        data = _decrypt_with_node(enc_file, master_key_path)
        return (data, enc_file) if data else (None, None)

    best, best_expires, best_path = None, 0, None
    for enc_file in glob.glob(os.path.join(store_dir, "*.enc")):
        data = _decrypt_with_node(enc_file, master_key_path)
        if not data or not data.get("accessToken"):
            continue
        exp = data.get("expiresAt", 0)
        if exp > best_expires:
            best, best_expires, best_path = data, exp, enc_file
    return best, best_path


def save_token_to_store(token_data, user_open_id):
    store_dir = _uat_store_dir()
    master_key_path = os.path.join(store_dir, "master.key")
    app_id = token_data["appId"]
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{app_id}:{user_open_id}") + ".enc"
    token_data["userOpenId"] = user_open_id
    _encrypt_with_node(json.dumps(token_data), master_key_path, os.path.join(store_dir, safe_name))


def _read_app_secret(app_id, secret_env="FEISHU_APP_SECRET"):
    secret = os.environ.get(secret_env, "")
    if secret:
        return secret

    # 优先从 openclaw.json / openclaw.jsonc 读取
    for name in ("openclaw.json", "openclaw.jsonc"):
        p = os.path.join(Path.home(), ".openclaw", name)
        try:
            with open(p) as f:
                content = re.sub(r"//.*$", "", f.read(), flags=re.MULTILINE)
                content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
                cfg = json.loads(content)
            feishu = cfg.get("channels", {}).get("feishu", {})

            def resolve_val(val):
                """解析 ${ENV_VAR} 引用"""
                if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
                    env_key = val[2:-1]
                    return os.environ.get(env_key, "")
                return val

            if resolve_val(feishu.get("appId", "")) == app_id:
                return resolve_val(feishu.get("appSecret", ""))
            for acct in feishu.get("accounts", {}).values():
                if isinstance(acct, dict) and resolve_val(acct.get("appId", "")) == app_id:
                    return resolve_val(acct.get("appSecret", ""))
        except Exception:
            continue

    # 回退到 .env 文件
    env_path = os.path.join(Path.home(), ".openclaw", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == secret_env:
                    return v.strip()
    except FileNotFoundError:
        pass
    # 兼容：如果自定义 env 名未匹配，回退尝试默认 FEISHU_APP_SECRET
    if secret_env != "FEISHU_APP_SECRET":
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "FEISHU_APP_SECRET":
                        return v.strip()
        except FileNotFoundError:
            pass
    return ""


# ── tenant_access_token ──

def get_tenant_token(app_id, app_secret):
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        f"{DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if data.get("code", -1) != 0:
        die(f"获取 tenant_access_token 失败: {data.get('msg', '')}")
    return data["tenant_access_token"]


# ── 发送飞书消息（用 tenant token 给 open_id 发）──

def send_interactive_card(tenant_token, open_id, card_json):
    """通过飞书 IM API 给用户发送 interactive 卡片消息"""
    body = json.dumps({
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json),
    }).encode()
    req = urllib.request.Request(
        f"{DOMAIN}/open-apis/im/v1/messages?receive_id_type=open_id",
        data=body,
        headers={
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"发送卡片失败: {e.read().decode(errors='replace')[:300]}", file=sys.stderr)
        return None


def build_auth_card(verification_url, expires_min, reason=None):
    """构建授权卡片 JSON（飞书 v1 卡片格式，IM API 直接发送）"""
    # applink 包装，在飞书客户端内以侧边栏打开授权页
    in_app_url = (
        f"{APPLINK_DOMAIN}/client/web_url/open"
        f"?mode=sidebar-semi&max_width=800&reload=false"
        f"&url={urllib.parse.quote(verification_url, safe='')}"
    )
    multi_url = {"url": in_app_url, "pc_url": in_app_url, "android_url": in_app_url, "ios_url": in_app_url}
    desc = reason or "需要你授权**妙记查看权限**才能继续操作。"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔐 需要授权妙记权限"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"{desc}\n点击下方按钮完成授权："}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "前往授权"}, "type": "primary", "multi_url": multi_url}
            ]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"授权链接将在 {expires_min} 分钟后失效"}]},
        ],
    }


def build_auth_success_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 妙记权限授权成功"},
            "template": "green",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "妙记权限已授权，正在为你获取妙记内容..."}},
        ],
    }


# ── Device Flow ──

def device_flow_start(app_id, app_secret, scope):
    basic = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    if "offline_access" not in scope:
        scope = f"{scope} offline_access"
    body = urllib.parse.urlencode({"client_id": app_id, "scope": scope}).encode()
    req = urllib.request.Request(
        f"{ACCOUNTS_DOMAIN}/oauth/v1/device_authorization",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        die(f"Device authorization 失败: {e.read().decode(errors='replace')[:300]}")
    return (
        data.get("verification_uri_complete", data.get("verification_uri", "")),
        data["device_code"],
        data.get("expires_in", 240),
        data.get("interval", 5),
    )


def device_flow_poll(app_id, app_secret, device_code, expires_in=240, interval=5):
    token_url = f"{DOMAIN}/open-apis/authen/v2/oauth/token"
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        poll_body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code, "client_id": app_id, "client_secret": app_secret,
        }).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(token_url, data=poll_body,
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})
            ) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                result = json.loads(e.read())
            except Exception:
                continue
        error = result.get("error")
        if not error and result.get("access_token"):
            now_ms = int(time.time() * 1000)
            return {
                "accessToken": result["access_token"],
                "refreshToken": result.get("refresh_token", ""),
                "expiresAt": now_ms + result.get("expires_in", 7200) * 1000,
                "refreshExpiresAt": now_ms + result.get("refresh_token_expires_in", 604800) * 1000,
                "scope": result.get("scope", ""), "grantedAt": now_ms, "appId": app_id,
            }
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = min(interval + 5, 60)
            continue
        if error == "access_denied":
            die("用户拒绝了授权")
        if error in ("expired_token", "invalid_grant"):
            die("授权码已过期，请重试")
    die("授权超时")


# ── 自动增量授权 ──

def auto_authorize(token_data, secret_env="FEISHU_APP_SECRET"):
    """token 过期或缺少妙记 scope 时，自动发卡片 + 轮询 + 保存新 token，返回新 accessToken"""
    app_id = token_data.get("appId", "")
    user_open_id = token_data.get("userOpenId", "")
    app_secret = _read_app_secret(app_id, secret_env)

    if not app_secret:
        die(
            f"token 不可用，且无法获取 appSecret 发起授权。\n"
            f"请检查环境变量 {secret_env} 或 ~/.openclaw/openclaw.json 配置。"
        )

    # 判断原因
    now_ms = int(time.time() * 1000)
    is_expired = token_data.get("expiresAt", 0) < now_ms + 5 * 60 * 1000
    needed = set(MINUTES_SCOPES.split())
    granted = set(token_data.get("scope", "").split())
    missing_scope = not needed.issubset(granted)

    if is_expired:
        reason = "你的飞书授权已过期，需要重新授权才能继续操作。"
        print("token 已过期，正在发送授权卡片...", file=sys.stderr)
    else:
        reason = "需要你授权**妙记查看权限**才能继续操作。"
        print("缺少妙记权限，正在发送授权卡片...", file=sys.stderr)

    # 只请求妙记 scope（飞书增量授权，新 token 包含旧 + 新）
    auth_scope = MINUTES_SCOPES

    # 发起 Device Flow
    url, device_code, expires_in, interval = device_flow_start(app_id, app_secret, auth_scope)
    expires_min = max(1, expires_in // 60)

    # 发授权卡片
    tenant_token = get_tenant_token(app_id, app_secret)
    card = build_auth_card(url, expires_min, reason)
    send_result = send_interactive_card(tenant_token, user_open_id, card)
    if send_result and send_result.get("code") == 0:
        print("授权卡片已发送，等待用户点击...", file=sys.stderr)
    else:
        print(f"卡片发送失败，请手动访问授权链接：\n{url}", file=sys.stderr)

    # 轮询等待
    new_token = device_flow_poll(app_id, app_secret, device_code, expires_in, interval)
    print("授权成功！", file=sys.stderr)

    # 发成功卡片
    try:
        send_interactive_card(tenant_token, user_open_id, build_auth_success_card())
    except Exception:
        pass

    # 保存
    if user_open_id:
        save_token_to_store(new_token, user_open_id)

    return new_token["accessToken"]


# ── 获取 token ──

def _token_is_valid(token_data):
    """检查 token 是否未过期且包含妙记 scope"""
    if not token_data or not token_data.get("accessToken"):
        return False
    now_ms = int(time.time() * 1000)
    # 提前 5 分钟视为过期
    if token_data.get("expiresAt", 0) < now_ms + 5 * 60 * 1000:
        return False
    needed = set(MINUTES_SCOPES.split())
    granted = set(token_data.get("scope", "").split())
    return needed.issubset(granted)


def get_token(enc_filename=None, secret_env="FEISHU_APP_SECRET"):
    user_token = os.environ.get("FEISHU_USER_TOKEN", "")
    if user_token:
        return user_token

    token_data, _ = read_plugin_store(enc_filename)
    if not token_data:
        die("无法获取 user_access_token。请先通过飞书 OpenClaw 插件完成用户授权。")

    # token 有效且有妙记权限 → 直接用
    if _token_is_valid(token_data):
        return token_data["accessToken"]

    # token 过期或缺少妙记权限 → 立即发授权卡片
    return auto_authorize(token_data, secret_env)


# ── API ──

def extract_minute_token(raw):
    m = re.search(r"/minutes/([A-Za-z0-9]{24})", raw)
    return m.group(1) if m else raw.strip()


def api_get(path, token, params=None):
    if params:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if qs:
            path = f"{path}?{qs}"
    req = urllib.request.Request(f"{DOMAIN}{path}", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body)
            die(f"[{e.code}] {err.get('code', '')} - {err.get('msg', body[:300])}")
        except json.JSONDecodeError:
            die(f"[{e.code}] {body[:500]}")
    try:
        data = json.loads(raw)
        if data.get("code", 0) != 0:
            die(f"[{data['code']}] {data.get('msg', '')}")
        return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode(errors="replace")


# ── commands ──

def cmd_info(args):
    t = get_token(args.enc_file, args.secret_env)
    data = api_get(f"/open-apis/minutes/v1/minutes/{extract_minute_token(args.minute_token)}", t, {"user_id_type": args.user_id_type})
    print(json.dumps(data.get("data", data), indent=2, ensure_ascii=False))

def cmd_transcript(args):
    t = get_token(args.enc_file, args.secret_env)
    params = {}
    if args.speaker: params["need_speaker"] = "true"
    if args.timestamp: params["need_timestamp"] = "true"
    if args.format: params["file_format"] = args.format
    result = api_get(f"/open-apis/minutes/v1/minutes/{extract_minute_token(args.minute_token)}/transcript", t, params)
    print(result if isinstance(result, str) else json.dumps(result.get("data", result), indent=2, ensure_ascii=False))

def cmd_media(args):
    t = get_token(args.enc_file, args.secret_env)
    data = api_get(f"/open-apis/minutes/v1/minutes/{extract_minute_token(args.minute_token)}/media", t)
    print(json.dumps(data.get("data", data), indent=2, ensure_ascii=False))

def cmd_statistics(args):
    t = get_token(args.enc_file, args.secret_env)
    data = api_get(f"/open-apis/minutes/v1/minutes/{extract_minute_token(args.minute_token)}/statistics", t, {"user_id_type": args.user_id_type})
    print(json.dumps(data.get("data", data), indent=2, ensure_ascii=False))

def cmd_artifacts(args):
    t = get_token(args.enc_file, args.secret_env)
    data = api_get(f"/open-apis/minutes/v1/minutes/{extract_minute_token(args.minute_token)}/artifacts", t)
    print(json.dumps(data.get("data", data), indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="飞书妙记 API")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("info", help="获取妙记基本信息")
    p.add_argument("minute_token"); p.add_argument("--user-id-type", default="open_id")
    p.add_argument("--enc-file", help="指定 .enc 文件名")
    p.add_argument("--secret-env", default="FEISHU_APP_SECRET", help="appSecret 的环境变量名")

    p = sub.add_parser("transcript", help="导出文字记录")
    p.add_argument("minute_token"); p.add_argument("--speaker", action="store_true")
    p.add_argument("--timestamp", action="store_true"); p.add_argument("--format", choices=["txt", "srt"])
    p.add_argument("--enc-file", help="指定 .enc 文件名")
    p.add_argument("--secret-env", default="FEISHU_APP_SECRET", help="appSecret 的环境变量名")

    p = sub.add_parser("media", help="获取音视频下载链接"); p.add_argument("minute_token")
    p.add_argument("--enc-file", help="指定 .enc 文件名")
    p.add_argument("--secret-env", default="FEISHU_APP_SECRET", help="appSecret 的环境变量名")
    p = sub.add_parser("statistics", help="获取统计数据")
    p.add_argument("minute_token"); p.add_argument("--user-id-type", default="open_id")
    p.add_argument("--enc-file", help="指定 .enc 文件名")
    p.add_argument("--secret-env", default="FEISHU_APP_SECRET", help="appSecret 的环境变量名")
    p = sub.add_parser("artifacts", help="获取 AI 产物"); p.add_argument("minute_token")
    p.add_argument("--enc-file", help="指定 .enc 文件名")
    p.add_argument("--secret-env", default="FEISHU_APP_SECRET", help="appSecret 的环境变量名")

    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(0)

    {"info": cmd_info, "transcript": cmd_transcript, "media": cmd_media,
     "statistics": cmd_statistics, "artifacts": cmd_artifacts}[args.command](args)

if __name__ == "__main__":
    main()
