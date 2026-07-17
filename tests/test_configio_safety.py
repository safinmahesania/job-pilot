"""The config path guard.

configio writes YAML files by name, and the name reaches it from API request bodies
(profile.yaml, companies-backup.yaml). If a caller could pass "../../etc/something" or a
name ending in a non-YAML extension, a write could land outside the config directory.
_path() is the guard that prevents that, and it had no test — so a refactor could
quietly weaken it and nothing would notice. These pin the guarantee: the resolved
path must sit directly in CONFIG_DIR and must end in .yaml/.yml.
"""
import pytest

from src import configio


class TestThePathGuardStaysInsideConfig:
    def test_a_plain_yaml_name_is_allowed(self):
        p = configio._path("profile.yaml")

        assert p.parent == configio.CONFIG_DIR
        assert p.name == "profile.yaml"

    def test_a_yml_extension_is_allowed(self):
        p = configio._path("companies.yml")

        assert p.name == "companies.yml"

    @pytest.mark.parametrize("attack", [
        "../secrets.yaml",
        "../../etc/passwd.yaml",
        "subdir/nested.yaml",
    ])
    def test_traversal_out_of_config_is_refused(self, attack):
        """Anything that resolves into a different directory is rejected. The guard
        leans on Path.resolve(), so this holds however the separators are spelled on
        the running platform (a backslash is a separator on Windows and an ordinary
        character on POSIX — resolve() gets that right either way)."""
        with pytest.raises(ValueError):
            configio._path(attack)

    @pytest.mark.parametrize("attack", [
        "profile.txt",
        "profile",
        "profile.yaml.exe",
        "passwd",
        ".bashrc",
    ])
    def test_a_non_yaml_target_is_refused(self, attack):
        """The guard also refuses to touch anything that is not a YAML file, so it
        can never be pointed at an arbitrary file type even inside the directory."""
        with pytest.raises(ValueError):
            configio._path(attack)
