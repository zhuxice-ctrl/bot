import nonebot
import asyncio
from pathlib import Path

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from src.plugins.video_report.service import YtDlpClient, _load_cached_douyin_info
from src.plugins.video_report.service import VideoReportConfig
from src.plugins.video_report.douyin_browser import (
    DouyinBrowserConfig,
    DouyinBrowserResolver,
    extract_douyin_video_id,
    load_netscape_cookies,
)
from src.plugins.video_report.workflow import (
    build_report_messages,
    build_pending_review_reminder,
    build_report_context,
    build_report_header,
    chat_mode_setting_key,
    extract_first_url,
    extract_preferred_video_url,
    normalize_chat_mode,
    summarize_metadata,
)


def test_extract_first_url_strips_common_trailing_punctuation():
    text = "看看这个 https://v.douyin.com/abc123/，挺有意思"

    assert extract_first_url(text) == "https://v.douyin.com/abc123/"


def test_extract_preferred_video_url_handles_douyin_share_text_with_title_prefix():
    text = (
        "/视频报告 4.30 复制打开抖音，看看【索亚加德的作品】"
        "redis之父亲自下场用纯C写的DeepSeekV... "
        "https://v.douyin.com/OuRFQhuTVEE/ o@QxS 07/13 :4pm OXz:/"
    )

    assert extract_preferred_video_url(text) == "https://v.douyin.com/OuRFQhuTVEE/"


def test_extract_preferred_video_url_skips_malformed_prefix_url():
    text = (
        "/视频报告 https: //4.30 复制打开抖音，看看【索亚加德的作品】"
        "redis之父亲自下场用纯C写的DeepSeekV... "
        "https://v.douyin.com/OuRFQhuTVEE/ o@QxS 07/13 :4pm OXz:/"
    )

    assert extract_preferred_video_url(text) == "https://v.douyin.com/OuRFQhuTVEE/"


def test_extract_preferred_video_url_prefers_douyin_over_other_links():
    text = "参考 https://example.com/a 真正链接 https://v.douyin.com/OuRFQhuTVEE/"

    assert extract_preferred_video_url(text) == "https://v.douyin.com/OuRFQhuTVEE/"


def test_normalize_chat_mode_accepts_shop_and_tk_aliases():
    assert normalize_chat_mode("shop") == "shop"
    assert normalize_chat_mode("/shop") == "shop"
    assert normalize_chat_mode("商品") == "shop"
    assert normalize_chat_mode("tk") == "tk"
    assert normalize_chat_mode("/tk") == "tk"
    assert normalize_chat_mode("视频") == "tk"
    assert normalize_chat_mode("bad") == ""


def test_chat_mode_setting_key_scopes_private_and_group_sessions():
    assert chat_mode_setting_key("private", "10001") == "chat_mode:private:10001"
    assert chat_mode_setting_key("group", "20002") == "chat_mode:group:20002"


def test_summarize_metadata_keeps_human_readable_core_fields():
    info = {
        "title": "测试标题",
        "uploader": "作者A",
        "duration": 65,
        "webpage_url": "https://www.douyin.com/video/123",
        "description": "这是一段视频描述",
        "view_count": 1000,
        "like_count": 88,
    }

    summary = summarize_metadata(info)

    assert "标题: 测试标题" in summary
    assert "作者: 作者A" in summary
    assert "时长: 65 秒" in summary
    assert "描述: 这是一段视频描述" in summary
    assert "点赞: 88" in summary


def test_build_report_header_adds_searchable_date_title_and_id():
    info = {
        "id": "7639687139814280500",
        "title": "测试标题",
        "uploader": "作者A",
        "webpage_url": "https://www.douyin.com/video/7639687139814280500",
    }

    header = build_report_header(info, generated_at=1710000000)

    assert "短视频内容分析报告" in header
    assert "生成日期: 2024-03-10 00:00" in header
    assert "标题: 测试标题" in header
    assert "作者: 作者A" in header
    assert "报告编号: VR-20240310-000000-7639687139814280500" in header
    assert "https://www.douyin.com/video/7639687139814280500" in header


def test_build_pending_review_reminder_uses_previous_report_title_and_date():
    reminder = build_pending_review_reminder(
        {
            "title": "上一条视频",
            "report_id": "VR-1",
            "generated_at_text": "2026-05-20 04:30",
        }
    )

    assert "上一份报告" in reminder
    assert "上一条视频" in reminder
    assert "2026-05-20 04:30" in reminder
    assert "/回顾" in reminder


def test_build_report_context_keeps_report_metadata_and_transcript_for_followup():
    context = build_report_context(
        index_info={
            "title": "测试标题",
            "report_id": "VR-1",
            "generated_at_text": "2026-05-20 05:10",
            "url": "https://v.douyin.com/test/",
        },
        report="短视频内容分析报告\n核心观点: 测试观点",
        metadata_summary="标题: 测试标题\n作者: 作者A",
        transcript="[0.0-1.0] 测试语音",
        warnings=["画面 OCR 未获取"],
    )

    assert context["title"] == "测试标题"
    assert context["report_id"] == "VR-1"
    assert "核心观点" in context["report"]
    assert "作者A" in context["metadata_summary"]
    assert "测试语音" in context["transcript"]
    assert context["warnings"] == ["画面 OCR 未获取"]


def test_build_report_messages_prefers_content_evidence_over_guessing():
    messages = build_report_messages(
        metadata_summary="标题: 测试标题\n作者: 作者A",
        transcript="今天讲三个选题方法。",
        visual_text="画面文字: 选题、爆款、复盘",
    )

    assert messages[0]["role"] == "system"
    assert "不要编造" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "今天讲三个选题方法" in messages[1]["content"]
    assert "画面文字" in messages[1]["content"]


def test_build_report_messages_requests_direct_operational_style():
    messages = build_report_messages(
        metadata_summary="标题: 测试标题",
        transcript="我平常用斗包处理代码。",
    )

    combined = "\n".join(message["content"] for message in messages)
    assert "不要使用“好的”" in combined
    assert "短视频内容分析报告" in combined
    assert "疑似错别字" in combined
    assert "未获取到对应语音证据" in combined
    assert "运营复盘" in combined


def test_ytdlp_client_uses_cookie_file_when_present(tmp_path):
    cookie_file = tmp_path / "douyin.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"url": url, "download": download}

    class FakeYtDlp:
        YoutubeDL = FakeYoutubeDL

    client = YtDlpClient(Path("vendor/yt-dlp"), cookie_file)
    client._load_ytdlp = lambda: FakeYtDlp()

    result = client._extract_info_sync("https://v.douyin.com/test/", False, None)

    assert result["url"] == "https://v.douyin.com/test/"
    assert captured["cookiefile"] == str(cookie_file)


def test_load_cached_douyin_info_maps_browser_detail(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "123456.json").write_text(
        """
        {
          "aweme_detail": {
            "desc": "测试视频",
            "create_time": 1710000000,
            "author": {"nickname": "作者", "uid": "u1"},
            "statistics": {"play_count": 10, "digg_count": 2, "comment_count": 1},
            "video": {
              "duration": 15000,
              "play_addr": {"url_list": ["https://example.com/video.mp4"]},
              "cover": {"url_list": ["https://example.com/cover.jpg"]}
            }
          }
        }
        """,
        encoding="utf-8",
    )

    info = _load_cached_douyin_info("https://www.douyin.com/video/123456", cache_dir)

    assert info
    assert info["title"] == "测试视频"
    assert info["uploader"] == "作者"
    assert info["duration"] == 15
    assert info["url"] == "https://example.com/video.mp4"
    assert info["thumbnail"] == "https://example.com/cover.jpg"


def test_extract_douyin_video_id_from_canonical_url():
    assert (
        extract_douyin_video_id("https://www.douyin.com/video/7639687139814280500")
        == "7639687139814280500"
    )


def test_load_netscape_cookies_supports_httponly(tmp_path):
    cookie_file = tmp_path / "douyin.txt"
    cookie_file.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1810324298\tsessionid\tabc",
                "www.douyin.com\tFALSE\t/\tFALSE\t0\ts_v_web_id\txyz",
            ]
        ),
        encoding="utf-8",
    )

    cookies = load_netscape_cookies(cookie_file)

    assert cookies[0]["domain"] == ".douyin.com"
    assert cookies[0]["httpOnly"] is True
    assert cookies[0]["secure"] is True
    assert cookies[0]["expires"] == 1810324298
    assert cookies[1]["name"] == "s_v_web_id"
    assert "expires" not in cookies[1]


def test_douyin_browser_resolver_uses_existing_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "7639687139814280500.json").write_text("{}", encoding="utf-8")
    resolver = DouyinBrowserResolver(DouyinBrowserConfig(cache_dir=cache_dir))

    result = asyncio.run(resolver.resolve("https://www.douyin.com/video/7639687139814280500"))

    assert result == "https://www.douyin.com/video/7639687139814280500"


def test_video_report_config_reads_max_concurrency(monkeypatch):
    class Config:
        video_report_max_concurrency = "2"

    monkeypatch.setattr(
        "src.plugins.video_report.service.get_driver",
        lambda: type("Driver", (), {"config": Config()})(),
    )

    assert VideoReportConfig.from_nonebot().max_concurrency == 2
