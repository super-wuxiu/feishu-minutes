#!/usr/bin/env python3
"""
检查妙记权限并在需要时发送授权卡片。

返回值（JSON）：
  {"status": "ok", "token": "..."}           — token 有效，可直接使用
  {"status": "waiting", "message": "..."}    — 已发送授权卡片，正在等待用户点击
  {"status": "authorized", "message": "..."} — 用户刚完成授权，token 已保存
  {"status": "error", "message": "..."}      — 出错

用法:
  python3 check_auth.py
"""

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
    """返回 token_data 或 None。如果指定 enc_filename 则只读该文件。"""
    store_dir = _uat_store_dir()
    if not os.path.isdir(store_dir):
        return None
    master_key_path = os.path.join(store_dir, "master.key")
    if not os.path.isfile(master_key_path):
        return None

    if enc_filename:
        # 指定文件
        enc_file = os.path.join(store_dir, enc_filename)
        if not enc_filename.endswith(".enc"):
            enc_file += ".enc"
        if not os.path.isfile(enc_file):
            return None
        return _decrypt_with_node(enc_file, master_key_path)

    # 扫描所有，选最新
    best, best_expires = None, 0
    for enc_file in glob.glob(os.path.join(store_dir, "*.enc")):
        data = _decrypt_with_node(enc_file, master_key_path)
        if not data or not data.get("accessToken"):
            continue
        exp = data.get("expiresAt", 0)
        if exp > best_expires:
            best, best_expires = data, exp
    return best


def save_token(token_data, user_open_id):
    store_dir = _uat_store_dir()
    master_key_path = os.path.join(store_dir, "master.key")
    app_id = token_data["appId"]
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{app_id}:{user_open_id}") + ".enc"
    token_data["userOpenId"] = user_open_id
    _encrypt_with_node(json.dumps(token_data), master_key_path, os.path.join(store_dir, safe_name))


def read_app_secret(app_id):
    secret = os.environ.get("FEISHU_APP_SECRET", "")
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
                if k.strip() == "FEISHU_APP_SECRET":
                    return v.strip()
    except FileNotFoundError:
        pass
    return ""


def get_tenant_token(app_id, app_secret):
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        f"{DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if data.get("code", -1) != 0:
        return None
    return data["tenant_access_token"]


def send_card(tenant_token, open_id, card_json):
    body = json.dumps({
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json),
    }).encode()
    req = urllib.request.Request(
        f"{DOMAIN}/open-apis/im/v1/messages?receive_id_type=open_id",
        data=body,
        headers={"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result.get("code") == 0
    except Exception:
        return False


def build_auth_card(verification_url, expires_min, reason):
    in_app_url = (
        f"{APPLINK_DOMAIN}/client/web_url/open"
        f"?mode=sidebar-semi&max_width=800&reload=false"
        f"&url={urllib.parse.quote(verification_url, safe='')}"
    )
    mu = {"url": in_app_url, "pc_url": in_app_url, "android_url": in_app_url, "ios_url": in_app_url}
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🔐 需要授权妙记权限"}, "template": "blue"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"{reason}\n点击下方按钮完成授权："}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "前往授权"}, "type": "primary", "multi_url": mu}
            ]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"授权链接将在 {expires_min} 分钟后失效"}]},
        ],
    }


def build_success_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "✅ 妙记权限授权成功"}, "template": "green"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "妙记权限已授权，正在为你获取妙记内容..."}},
        ],
    }


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
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return (
        data.get("verification_uri_complete", data.get("verification_uri", "")),
        data["device_code"], data.get("expires_in", 240), data.get("interval", 5),
    )


def device_flow_poll(app_id, app_secret, device_code, expires_in, interval):
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
            return None
        if error in ("expired_token", "invalid_grant"):
            return None
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="检查妙记权限授权")
    parser.add_argument("--enc-file", help="指定 token store 中的 .enc 文件名（多用户环境）")
    args = parser.parse_args()

    token_data = read_plugin_store(args.enc_file)
    if not token_data:
        print(json.dumps({"status": "error", "message": "无法读取 token store，请先通过飞书插件完成基础授权。"}))
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    is_expired = token_data.get("expiresAt", 0) < now_ms + 5 * 60 * 1000
    has_scope = set(MINUTES_SCOPES.split()).issubset(set(token_data.get("scope", "").split()))

    # token 有效且有妙记权限
    if not is_expired and has_scope:
        print(json.dumps({"status": "ok"}))
        return

    # 需要授权
    app_id = token_data.get("appId", "")
    user_open_id = token_data.get("userOpenId", "")
    app_secret = read_app_secret(app_id)
    if not app_secret:
        print(json.dumps({"status": "error", "message": "无法获取 appSecret，请检查 ~/.openclaw/.env"}))
        sys.exit(1)

    reason = "你的飞书授权已过期，需要重新授权。" if is_expired else "需要你授权**妙记查看权限**才能继续操作。"

    # 只请求妙记相关 scope（飞书 OAuth 是增量授权，新 token 会包含旧 + 新 scope）
    auth_scope = MINUTES_SCOPES

    # Device Flow + 发卡片
    url, device_code, expires_in, interval = device_flow_start(app_id, app_secret, auth_scope)
    tt = get_tenant_token(app_id, app_secret)
    if not tt:
        print(json.dumps({"status": "error", "message": "无法获取 tenant_access_token"}))
        sys.exit(1)

    card = build_auth_card(url, max(1, expires_in // 60), reason)
    card_sent = send_card(tt, user_open_id, card)

    print(json.dumps({"status": "waiting", "message": "已发送授权卡片，等待用户点击授权..."}))
    sys.stdout.flush()

    # 轮询等待
    new_token = device_flow_poll(app_id, app_secret, device_code, expires_in, interval)
    if not new_token:
        print(json.dumps({"status": "error", "message": "授权超时或被拒绝，请重试。"}))
        sys.exit(1)

    # 保存 + 发成功卡片
    if user_open_id:
        save_token(new_token, user_open_id)
    try:
        send_card(tt, user_open_id, build_success_card())
    except Exception:
        pass

    print(json.dumps({"status": "authorized", "message": "授权成功，妙记权限已获取。"}))


if __name__ == "__main__":
    main()
