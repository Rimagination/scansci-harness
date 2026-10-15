from scansci_html.local_model_recommendation import recommend_chat_model


def test_eight_gigabyte_gpu_recommends_four_billion_parameter_model():
    hardware = {"platform": "Windows", "vram_gib": 8.0, "ram_gib": 32.0, "storage_free_gib": 55.0}
    ollama = {"reachable": True, "base_url": "http://127.0.0.1:11434", "models": [{"name": "qwen3.5:4b"}]}

    recommendation = recommend_chat_model(hardware, ollama)

    assert recommendation["id"] == "qwen3.5:4b"
    assert recommendation["installed"] is True
    assert recommendation["storage_blocked"] is False
    assert recommendation["context_tokens"] == 32768


def test_recommendation_does_not_confuse_another_model_with_the_selected_model():
    hardware = {"platform": "Windows", "vram_gib": 8.0, "ram_gib": 32.0, "storage_free_gib": 3.0}
    ollama = {"reachable": True, "base_url": "http://127.0.0.1:11434", "models": [{"name": "minicpm-v4.6:latest"}]}

    recommendation = recommend_chat_model(hardware, ollama)

    assert recommendation["id"] == "qwen3.5:4b"
    assert recommendation["installed"] is False
    assert recommendation["storage_blocked"] is True


def test_smaller_machine_receives_a_smaller_single_recommendation():
    ollama = {"reachable": True, "base_url": "http://127.0.0.1:11434", "models": []}

    assert recommend_chat_model({"platform": "Windows", "vram_gib": 4, "ram_gib": 16}, ollama)["id"] == "qwen3.5:2b"
    assert recommend_chat_model({"platform": "Windows", "vram_gib": 0, "ram_gib": 12}, ollama)["id"] == "qwen3.5:2b"
    assert recommend_chat_model({"platform": "Windows", "vram_gib": 0, "ram_gib": 4}, ollama)["available"] is False


def test_twelve_gigabyte_gpu_can_recommend_nine_billion_parameter_model():
    ollama = {"reachable": True, "base_url": "http://127.0.0.1:11434", "models": []}

    assert recommend_chat_model({"platform": "Windows", "vram_gib": 12, "ram_gib": 16}, ollama)["id"] == "qwen3.5:9b"


def test_new_users_are_not_prompted_to_install_ollama_for_a_primary_model():
    hardware = {"platform": "Windows", "vram_gib": 8, "ram_gib": 32}
    for ollama in (
        {"reachable": False, "base_url": "http://127.0.0.1:11434", "models": []},
        {"reachable": True, "base_url": "http://example.com:11434", "models": [{"name": "qwen3.5:9b"}]},
    ):
        recommendation = recommend_chat_model(hardware, ollama)

        assert recommendation["available"] is False
        assert "一体化本地主力模型" in recommendation["reason"]
