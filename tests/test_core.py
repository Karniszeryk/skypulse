import unittest

from fastapi.testclient import TestClient

import app as web_app
from backend.airports import (
    estimate_baggage_fee_pln,
    get_airport_meta,
    is_comfortable_hours,
    is_weekend_friendly,
    resolve_destination_iata_list,
    search_world_airports,
)


class SkyPulseCoreTests(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        client = TestClient(web_app.app)
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("service"), "skypulse")

    def test_multi_city_polish_origin_search(self) -> None:
        hits = search_world_airports("Kraków, Katowice", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["kind"], "multi")
        self.assertEqual(hits[0]["codes"], "KRK,KTW")

    def test_polish_city_and_longhaul_presets(self) -> None:
        ed = search_world_airports("Edynburg", limit=3)
        self.assertTrue(ed)
        self.assertIn("EDI", ed[0]["codes"])

        nz = search_world_airports("Nowa Zelandia", limit=3)
        self.assertTrue(nz)
        self.assertEqual(nz[0]["preset_id"], "NZ")
        self.assertGreaterEqual(nz[0]["suggested_price"], 7000)
        self.assertGreaterEqual(nz[0]["suggested_travel_h"], 48)

        bb = search_world_airports("Bora Bora", limit=3)
        self.assertTrue(bb)
        self.assertEqual(bb[0]["suggested_stops"], 3)
        self.assertGreaterEqual(bb[0]["suggested_price"], 9000)

    def test_destination_resolution_and_meta(self) -> None:
        self.assertEqual(resolve_destination_iata_list("Edynburg"), ["EDI"])
        self.assertEqual(
            resolve_destination_iata_list("Edynburg, Wielka Brytania (EDI)"),
            ["EDI"],
        )
        self.assertEqual(resolve_destination_iata_list("WORLD"), [])

        ktw = get_airport_meta("KTW")
        self.assertEqual(ktw["country_code"], "PL")
        self.assertEqual(ktw["city"], "Katowice")

    def test_baggage_pricing_modes(self) -> None:
        fee_bp, _ = estimate_baggage_fee_pln("FR", 0, "roundtrip", "backpack", 4)
        self.assertEqual(fee_bp, 0.0)

        fee_fs, _ = estimate_baggage_fee_pln("LO", 0, "roundtrip", "cabin10kg", 2)
        self.assertEqual(fee_fs, 0.0)

        fee_shared_4, _ = estimate_baggage_fee_pln("FR", 0, "roundtrip", "shared20kg", 4)
        fee_single, _ = estimate_baggage_fee_pln("FR", 0, "roundtrip", "checked20kg", 1)
        self.assertAlmostEqual(fee_shared_4 * 4, fee_single, delta=0.1)

    def test_time_filters(self) -> None:
        self.assertTrue(
            is_weekend_friendly("2026-12-04T18:00:00", "2026-12-06T19:00:00")
        )
        self.assertFalse(
            is_weekend_friendly("2026-12-01T10:00:00", "2026-12-06T19:00:00")
        )
        self.assertTrue(
            is_comfortable_hours("2026-12-04T09:15:00", "2026-12-04T11:30:00")
        )
        self.assertFalse(
            is_comfortable_hours("2026-12-04T05:45:00", "2026-12-04T08:00:00")
        )


if __name__ == "__main__":
    unittest.main()
