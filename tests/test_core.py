import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


xboard_sync = load_module("xboard_sync", "sync/xboard_sync.py")
xboard_report = load_module("xboard_report", "sync/xboard_report.py")


class NodeParsingTests(unittest.TestCase):
    def test_sync_parses_multi_node_env_with_aliases(self):
        env = {"NODES": "3047:vless, 8881:ss, 8882:v2ray"}

        self.assertEqual(
            xboard_sync.get_nodes(env),
            [("3047", "vless"), ("8881", "shadowsocks"), ("8882", "vmess")],
        )

    def test_report_parses_legacy_single_node_env(self):
        env = {"NODE_ID": "3047", "NODE_TYPE": "ss"}

        self.assertEqual(xboard_report.get_nodes(env), [("3047", "shadowsocks")])

    def test_empty_nodes_are_rejected(self):
        for module in (xboard_sync, xboard_report):
            with self.subTest(module=module.__name__):
                with self.assertRaises(RuntimeError):
                    module.get_nodes({"NODES": " , "})

    def test_incomplete_node_entries_are_rejected(self):
        for module in (xboard_sync, xboard_report):
            with self.subTest(module=module.__name__):
                with self.assertRaises(RuntimeError):
                    module.get_nodes({"NODES": "3047:"})
                with self.assertRaises(RuntimeError):
                    module.get_nodes({"NODES": ":vless"})


class SyncConfigTests(unittest.TestCase):
    def test_client_emails_are_scoped_by_node_for_all_protocols(self):
        user_resp = {
            "users": [
                {
                    "id": 1485,
                    "uuid": "00000000-0000-0000-0000-000000000001",
                    "password": "trojan-password",
                }
            ]
        }

        self.assertEqual(
            xboard_sync.build_vless_clients(user_resp, flow=None, node_id="3047")[0]["email"],
            "3047:1485",
        )
        self.assertEqual(
            xboard_sync.build_vmess_clients(user_resp, node_id="3047")[0]["email"],
            "3047:1485",
        )
        self.assertEqual(
            xboard_sync.build_trojan_clients(user_resp, node_id="3047")[0]["email"],
            "3047:1485",
        )
        self.assertEqual(
            xboard_sync.build_ss_clients(user_resp, method="aes-128-gcm", node_id="3047")[0]["email"],
            "3047:1485",
        )

    def test_vless_inbound_uses_scoped_email_and_expected_port(self):
        config_resp = {
            "data": {
                "protocol": "vless",
                "server_port": 443,
                "network": "tcp",
                "tls": 0,
            }
        }
        user_resp = {
            "users": [
                {
                    "id": 1485,
                    "uuid": "00000000-0000-0000-0000-000000000001",
                }
            ]
        }

        inbound = xboard_sync.build_vless_inbound(config_resp, user_resp, node_id="3047")

        self.assertEqual(inbound["tag"], "vless-443")
        self.assertEqual(inbound["settings"]["clients"][0]["email"], "3047:1485")

    def test_xray_config_includes_stats_api_and_user_policies(self):
        config = xboard_sync.build_xray_config([])

        self.assertIn("stats", config)
        self.assertEqual(config["inbounds"][0]["tag"], "api")
        self.assertTrue(config["policy"]["levels"]["0"]["statsUserUplink"])
        self.assertTrue(config["policy"]["levels"]["0"]["statsUserDownlink"])


class ReportTrafficTests(unittest.TestCase):
    def test_parse_traffic_by_node_aggregates_scoped_and_legacy_stats(self):
        stats = {
            "stat": [
                {"name": "user>>>3047:1485>>>traffic>>>uplink", "value": "100"},
                {"name": "user>>>3047:1485>>>traffic>>>downlink", "value": "200"},
                {"name": "user>>>8881:42>>>traffic>>>uplink", "value": "7"},
                {"name": "user>>>99>>>traffic>>>downlink", "value": "5"},
                {"name": "inbound>>>api>>>traffic>>>uplink", "value": "999"},
            ]
        }

        scoped, legacy = xboard_report.parse_traffic_by_node(stats)

        self.assertEqual(scoped, {"3047": {1485: [100, 200]}, "8881": {42: [7, 0]}})
        self.assertEqual(legacy, {99: [0, 5]})

    def test_main_posts_scoped_traffic_to_each_configured_node(self):
        stats = {
            "stat": [
                {"name": "user>>>3047:1485>>>traffic>>>uplink", "value": "100"},
                {"name": "user>>>8881:42>>>traffic>>>downlink", "value": "7"},
            ]
        }
        env = {
            "PANEL_URL": "https://panel.example.com",
            "PANEL_TOKEN": "token",
            "NODES": "3047:vless,8881:ss",
        }

        with mock.patch.object(xboard_report, "load_env", return_value=env), \
             mock.patch.object(xboard_report, "run_statsquery", return_value=stats), \
             mock.patch.object(xboard_report, "post_traffic") as post_traffic:
            xboard_report.main()

        calls = [
            (args[1], args[2], args[3])
            for args, _kwargs in post_traffic.call_args_list
        ]
        self.assertEqual(
            calls,
            [
                ("3047", "vless", {1485: [100, 0]}),
                ("8881", "shadowsocks", {42: [0, 7]}),
            ],
        )

    def test_main_merges_legacy_traffic_for_single_node_configs(self):
        stats = {"stat": [{"name": "user>>>1485>>>traffic>>>uplink", "value": "100"}]}
        env = {
            "PANEL_URL": "https://panel.example.com",
            "PANEL_TOKEN": "token",
            "NODE_ID": "3047",
            "NODE_TYPE": "vless",
        }

        with mock.patch.object(xboard_report, "load_env", return_value=env), \
             mock.patch.object(xboard_report, "run_statsquery", return_value=stats), \
             mock.patch.object(xboard_report, "post_traffic") as post_traffic:
            xboard_report.main()

        post_traffic.assert_called_once_with(env, "3047", "vless", {1485: [100, 0]})

    def test_post_traffic_skips_empty_payload(self):
        with mock.patch.object(xboard_report.requests, "post") as post, \
             mock.patch("builtins.print"):
            xboard_report.post_traffic(
                {"PANEL_URL": "https://panel.example.com", "PANEL_TOKEN": "token"},
                "3047",
                "vless",
                {},
            )

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
