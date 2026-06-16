"""
/admin 端点测试 - 验证管理面板功能
"""
import json
import sys
import httpx

FMR_URL = "http://127.0.0.1:19876"


def test_admin_html():
    """/admin 返回 HTML dashboard"""
    print("\n=== TEST ADMIN: /admin 返回 HTML ===")
    r = httpx.get(f"{FMR_URL}/admin", timeout=5)
    assert r.status_code == 200, f"status={r.status_code}"
    assert "text/html" in r.headers.get("content-type", ""), f"content-type={r.headers.get('content-type')}"
    body = r.text
    assert "<html" in body.lower() or "<!doctype" in body.lower(), "不是 HTML"
    # 期望有 provider 名称出现在 dashboard
    assert "mock_free_pattern" in body, "dashboard 没显示 provider 名称"
    print(f"  ✅ /admin 返回 {len(body)}B HTML, 含 provider 信息")
    return True


def test_admin_discover():
    """/admin/discover POST 触发重新发现"""
    print("\n=== TEST ADMIN: /admin/discover 手动触发发现 ===")
    r = httpx.post(f"{FMR_URL}/admin/discover", timeout=15)
    assert r.status_code == 200, f"status={r.status_code}, body={r.text[:200]}"
    data = r.json()
    print(f"  返回: {json.dumps(data, indent=2)[:500]}")
    # 期望 4 个 provider 都被发现
    if "results" in data:
        assert len(data["results"]) >= 4, f"discover 结果少于 4: {len(data['results'])}"
        for pid, result in data["results"].items():
            assert result.get("model_count", 0) > 0, f"{pid} 没发现模型"
            print(f"  ✅ {pid}: {result.get('free_model_count', 0)} free / {result.get('model_count', 0)} total")
    return True


def test_admin_health_check():
    """/admin/health-check POST 触发主动健康检查"""
    print("\n=== TEST ADMIN: /admin/health-check 主动检查 ===")
    r = httpx.post(f"{FMR_URL}/admin/health-check", timeout=15)
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    print(f"  返回: {json.dumps(data, indent=2)[:500]}")
    return True


def test_v1_models_structure():
    """/v1/models 返回 OpenAI 兼容格式"""
    print("\n=== TEST ADMIN: /v1/models 结构 ===")
    r = httpx.get(f"{FMR_URL}/v1/models", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data.get("object") == "list", f"object={data.get('object')}"
    assert "data" in data
    assert len(data["data"]) > 0
    # 验证每个 model 含必备字段
    for m in data["data"][:3]:
        assert "id" in m, f"model 缺 id: {m}"
        assert "object" in m, f"model 缺 object: {m}"
        assert m["object"] == "model", f"object={m['object']}"
    print(f"  ✅ /v1/models 返回 {len(data['data'])} 个模型, OpenAI 兼容格式")
    return True


def test_v1_providers_detail():
    """/v1/providers 返回详细 provider 状态"""
    print("\n=== TEST ADMIN: /v1/providers 详情 ===")
    r = httpx.get(f"{FMR_URL}/v1/providers", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data.get("object") == "list"
    assert "data" in data
    providers = data["data"]
    assert len(providers) == 4, f"应该 4 个 provider, 实际 {len(providers)}"
    for p in providers:
        assert "id" in p
        assert "base_url" in p
        assert "key_count" in p
        assert "status" in p
        assert "free_model_count" in p
        assert "model_rules" in p
        assert "keys" in p
    print(f"  ✅ {len(providers)} 个 provider, 字段完整")
    return True


def main():
    print("检查 fmr 运行状态...")
    try:
        r = httpx.get(f"{FMR_URL}/health", timeout=3)
        if r.status_code != 200:
            print(f"❌ fmr /health 返回 {r.status_code}")
            return 1
    except Exception as e:
        print(f"❌ fmr 未运行: {e}")
        return 1
    print(f"✅ fmr 运行中 @ {FMR_URL}")

    results = []
    results.append(("/admin HTML", test_admin_html()))
    results.append(("/admin/discover", test_admin_discover()))
    results.append(("/admin/health-check", test_admin_health_check()))
    results.append(("/v1/models", test_v1_models_structure()))
    results.append(("/v1/providers", test_v1_providers_detail()))

    print("\n" + "=" * 50)
    print("ADMIN 测试结果:")
    passed = 0
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
        if ok:
            passed += 1
    print(f"\n通过: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
