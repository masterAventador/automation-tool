#!/usr/bin/env python3
import base64, hashlib, hmac, json, signal, sys
bootstrap = json.loads(sys.stdin.readline())
key = bytes.fromhex(bootstrap["local_session_token"])
def encoded(domain, parts):
    message = domain + b"\0".join(part.encode() for part in parts)
    return "atlcp1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
def lifecycle(event):
    message = b"automation-tool.local-executor-event.v1\0" + event.encode() + b"\0" + b"1.0"
    proof = "atlep1." + base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=").decode()
    print(json.dumps({"authenticationProof": proof, "event": event, "protocolVersion": "1.0"}, separators=(",", ":")), flush=True)
signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
lifecycle("executor.healthy")
for line in sys.stdin:
    command = json.loads(line)
    kind = command["commandType"]
    if kind == "douyin.publish.preflight":
        parts = [command["commandId"], kind, command["executablePath"], command["profileDirectory"],
                 "1" if command["headless"] else "0", command["publishJobId"], command["artifactPath"],
                 command["title"], command["description"], command["protocolVersion"]]
        domain = b"automation-tool.local-executor-publish-command.v1\0"
        state = "publish_pre_submit_ready"
        flow = "douyin.publish-preflight.v1"
    elif kind == "douyin.publish.dispatch":
        parts = [command["commandId"], kind, command["publishJobId"], command["confirmationId"], command["protocolVersion"]]
        domain = b"automation-tool.local-executor-publish-dispatch.v1\0"
        state = "publish_verified"
        flow = "douyin.publish-release.v1"
    else:
        # 被拒的那条请求永远不该走到这里；真到了就让测试当场看见。
        raise AssertionError(kind)
    assert hmac.compare_digest(command["authenticationProof"], encoded(domain, parts)), kind
    result = {"authenticationProof": encoded(b"automation-tool.local-executor-result.v1\0", [command["commandId"], state, "1.0"]),
              "commandId": command["commandId"], "event": "platform.command.completed",
              "flowVersion": flow, "platform": "douyin",
              "protocolVersion": "1.0", "state": state}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
lifecycle("executor.stopped")
