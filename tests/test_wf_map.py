from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from kit.workflow import map_render


ROOT = Path(__file__).resolve().parents[1]


class _MapHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.manifest = ""
        self.in_manifest = False
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.attributes.append((tag, dict(attrs)))
        if tag == "script" and dict(attrs).get("id") == "keel-harness-manifest":
            self.in_manifest = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_manifest = False

    def handle_data(self, data):
        if self.in_manifest:
            self.manifest += data


class WorkflowMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map = json.loads((ROOT / "kit/workflow/map.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((ROOT / "kit/workflow/manifest.json").read_text(encoding="utf-8"))
        cls.catalogue = json.loads((ROOT / "kit/catalogue.json").read_text(encoding="utf-8"))

    def test_install_ids_resolve_and_cover_manifest(self):
        manifest_ids = {item["id"] for item in self.manifest["items"]}
        known_ids = manifest_ids | {item["id"] for item in self.catalogue}
        used = set()
        for stage in self.map["stages"] + self.map["side"]:
            used.update(stage["install"])
            for node in stage["nodes"]:
                used.update(node["install"])
        self.assertEqual(used - known_ids, set())
        self.assertEqual(manifest_ids - used, set())

    def test_all_translations_are_nonempty(self):
        def check(value):
            if isinstance(value, dict):
                if "en" in value or "ko" in value:
                    self.assertEqual(set(value), {"en", "ko"})
                    self.assertTrue(value["en"].strip())
                    self.assertTrue(value["ko"].strip())
                else:
                    for child in value.values():
                        check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(self.map)

    def test_hook_paths_name_catalogue_scripts(self):
        scripts = {item["script"] for item in self.catalogue if item.get("script")}
        paths = re.findall(r"\{KEEL_HOME\}/hooks/([^\s\"}]+)", json.dumps(self.map))
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(path, scripts)

    def test_manifest_titles_are_bilingual(self):
        for item in self.manifest["items"]:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["title"].get("en", "").strip())
                self.assertTrue(item["title"].get("ko", "").strip())

    def test_edges_refer_to_nodes_in_their_stage(self):
        for stage in self.map["stages"] + self.map["side"]:
            ids = {node["id"] for node in stage["nodes"]}
            for edge in stage["edges"]:
                with self.subTest(stage=stage["id"], edge=edge[:2]):
                    self.assertIn(edge[0], ids)
                    self.assertIn(edge[1], ids)

    def test_translation_helper_is_the_only_t_declaration(self):
        template = (ROOT / "kit/workflow/map_template.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", template, re.S).group(1)
        self.assertRegex(script, r"\bconst\s+t\s*=\s*obj\s*=>")
        self.assertEqual(re.findall(r"\b(?:const|let|var)\s+(t)\b", script), ["t"])
        self.assertIsNone(re.search(
            r"\b(?:const|let|var)\s*[\[{][^\]}]*\bt\b[^\]}]*[\]}]\s*=", script
        ))
        self.assertIsNone(re.search(r"(?:\([^)]*\bt\b[^)]*\)|\bt\b)\s*=>", script))
        self.assertIsNone(re.search(r"\bfunction(?:\s+\w+)?\s*\([^)]*\bt\b[^)]*\)", script))

    def test_render_is_self_contained_and_embeds_manifest(self):
        html = map_render.render(self.map, self.manifest, self.catalogue, "en", {
            "KEEL_HOME": "/tmp/keel", "KB_PATH": "/tmp/kb",
            "CLI_HOME": {"claude": "/tmp/claude", "codex": "/tmp/codex"},
        })
        parser = _MapHTML()
        parser.feed(html)
        self.assertNotIn("<link", html.lower())
        self.assertNotIn("@import", html.lower())
        self.assertIsNone(re.search(r"url\(\s*['\"]?https?://", html, re.I))
        for tag, attrs in parser.attributes:
            self.assertNotIn("src", attrs, tag)
            if "href" in attrs:
                self.assertTrue(attrs["href"].startswith("#") or
                                attrs["href"] == "https://github.com/badjin/keel")
        embedded = json.loads(parser.manifest)
        self.assertEqual({item["id"] for item in embedded["items"]},
                         {item["id"] for item in self.manifest["items"]})
