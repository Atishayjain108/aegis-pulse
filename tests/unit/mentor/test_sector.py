"""Tests for SectorRouter + SectorPack (Phase M1)."""

from __future__ import annotations

from aegis.mentor.packs.d2c_india import D2CIndiaPack
from aegis.mentor.schemas import SECTOR_UNDECIDED
from aegis.mentor.sector import BaselinePack, SectorPack, SectorRouter


class TestSectorRouter:
    def test_d2c_keywords_normalize_to_d2c_india(self) -> None:
        router = SectorRouter()
        for text in ("I want to sell products online", "dropshipping on flipkart", "start a d2c apparel brand"):
            assert router.normalize(text) == "d2c_india"

    def test_local_shop_normalizes_to_local_retail(self) -> None:
        router = SectorRouter()
        assert router.normalize("I run a kirana shop") == "local_retail"

    def test_freelance_normalizes_to_creator_services(self) -> None:
        router = SectorRouter()
        assert router.normalize("I'm a freelance designer") == "creator_services"

    def test_unknown_field_is_undecided_not_fabricated(self) -> None:
        router = SectorRouter()
        assert router.normalize("hello") == SECTOR_UNDECIDED
        assert router.normalize("") == SECTOR_UNDECIDED

    def test_known_tag_passthrough(self) -> None:
        router = SectorRouter()
        assert router.normalize("d2c_india") == "d2c_india"

    def test_route_deep_pack_for_d2c(self) -> None:
        router = SectorRouter()
        pack = router.route("d2c_india")
        assert isinstance(pack, SectorPack)
        assert pack.is_deep is True
        assert pack.benchmarks()["min_viable_gross_margin_pct"] == 0.40

    def test_route_baseline_for_undecided(self) -> None:
        router = SectorRouter()
        pack = router.route(SECTOR_UNDECIDED)
        assert isinstance(pack, BaselinePack)
        assert pack.is_deep is False
        # Baseline never fabricates sector numbers.
        assert pack.benchmarks() == {}

    def test_deep_sectors_registered(self) -> None:
        router = SectorRouter()
        assert "d2c_india" in router.deep_sectors()


class TestD2CIndiaPack:
    def test_pack_satisfies_protocol(self) -> None:
        assert isinstance(D2CIndiaPack(), SectorPack)

    def test_grounded_knowledge_present(self) -> None:
        pack = D2CIndiaPack()
        knowledge = pack.knowledge()
        assert "amazon_in" in knowledge["marketplaces"]
        assert knowledge["tier"] == "deep"
        assert pack.region_default == "IN"
