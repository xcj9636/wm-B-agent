import importlib


def test_task_queue_runtime_dependencies_are_installed():
    assert importlib.import_module("celery")
    assert importlib.import_module("flower")


def test_rag_skill_imports_with_a_supported_vector_stack():
    assert importlib.import_module("chromadb")
    module = importlib.import_module("app.skills.skill_rag")

    assert module.RagSkill.name == "rag_skill"
