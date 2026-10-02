"""Resolução do texto da localidade para a hierarquia do Catalyst Center."""

from __future__ import annotations

import unittest

from catalyst_center.sites import matching_site_hierarchies, site_ids_under


SITES = [
    {"name": "ES", "groupNameHierarchy": "Global/ES", "id": "area"},
    {"name": "Serra", "groupNameHierarchy": "Global/ES/Serra", "id": "building"},
    {"name": "Andar 1", "groupNameHierarchy": "Global/ES/Serra/Andar 1", "id": "floor"},
    {"name": "Serra", "groupNameHierarchy": "Global/RJ/Serra", "id": "other"},
]


class SiteMatchTests(unittest.TestCase):
    def test_keeps_the_shortest_hierarchy_of_each_branch(self) -> None:
        self.assertEqual(
            matching_site_hierarchies(SITES, "serra"),
            ["Global/ES/Serra", "Global/RJ/Serra"],
        )

    def test_collects_the_building_and_its_floors(self) -> None:
        hierarchies = matching_site_hierarchies(SITES, "Global/ES/Serra")
        self.assertEqual(site_ids_under(SITES, hierarchies), ["building", "floor"])
