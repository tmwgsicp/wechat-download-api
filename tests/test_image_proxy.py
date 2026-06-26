import unittest
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "image_proxy.py"
spec = importlib.util.spec_from_file_location("image_proxy", MODULE_PATH)
image_proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_proxy)

proxy_content_images = image_proxy.proxy_content_images
proxy_image_url = image_proxy.proxy_image_url


class ImageProxyTests(unittest.TestCase):
    def test_proxy_image_url_rewrites_existing_proxy_with_current_base_url(self):
        old_url = (
            "http://localhost:5000/api/image?url="
            "https%3A%2F%2Fmmbiz.qpic.cn%2Fmmbiz_png%2Fabc%2F640%3Fwx_fmt%3Dpng"
        )

        proxied = proxy_image_url(old_url, "http://localhost:8082")

        self.assertTrue(proxied.startswith("http://localhost:8082/api/image?url="))
        self.assertNotIn("localhost:5000", proxied)
        self.assertIn("%2Fmmbiz_png%2Fabc%2F640", proxied)

    def test_proxy_content_images_rewrites_cached_proxy_urls(self):
        html = (
            '<p><img data-src="http://localhost:5000/api/image?url='
            'https%3A%2F%2Fmmbiz.qpic.cn%2Fmmbiz_jpg%2Fabc%2F640%3Fwx_fmt%3Djpeg" /></p>'
        )

        proxied = proxy_content_images(html, "https://rss.example.com")

        self.assertIn('data-src="https://rss.example.com/api/image?url=', proxied)
        self.assertIn('src="https://rss.example.com/api/image?url=', proxied)
        self.assertNotIn("localhost:5000", proxied)


if __name__ == "__main__":
    unittest.main()
