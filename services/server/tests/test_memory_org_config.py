from src.mcp import memory as memory_mod
from src.org_settings import OrgSettings


def test_build_memory_config_uses_org_settings():
    s = OrgSettings(
        llm_provider="anthropic", llm_model="claude-haiku-4-5",
        anthropic_api_key="ak", embeddings_dims=1024, embeddings_api_key="ek",
    )
    cfg = memory_mod._build_memory_config(
        s, "postgresql://u:p@h:5432/db", "cf_memories_org7"
    )
    assert cfg["llm"]["provider"] == "anthropic"
    assert cfg["llm"]["config"]["api_key"] == "ak"
    assert cfg["embedder"]["config"]["embedding_dims"] == 1024
    assert cfg["vector_store"]["config"]["collection_name"] == "cf_memories_org7"
    assert cfg["vector_store"]["config"]["embedding_model_dims"] == 1024


def test_collection_name_stays_shared_when_config_matches_bootstrap():
    bootstrap = OrgSettings()
    assert memory_mod._memory_collection_name(7, OrgSettings(), bootstrap) == "cf_memories"
    custom = OrgSettings(embeddings_dims=1024)
    assert memory_mod._memory_collection_name(7, custom, bootstrap) == "cf_memories_org7"
