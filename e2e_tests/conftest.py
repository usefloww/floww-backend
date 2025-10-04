import os
import requests

HTTP_AUTH_USER = os.getenv("HTTP_AUTH_USER")
HTTP_AUTH_PASSWORD = os.getenv("HTTP_AUTH_PASSWORD")
MS_URL = os.getenv("MS_URL", "http://localhost:8000")

kwargs = {}
if HTTP_AUTH_USER and HTTP_AUTH_PASSWORD:
    kwargs["auth"] = (HTTP_AUTH_USER, HTTP_AUTH_PASSWORD)


class Client:
    def _url(self, path):
        return f"{MS_URL}{path}"

    def get(self, url):
        assert url[0] == "/", "URL must start with /"
        return requests.get(self._url(url), **kwargs)

    def post(self, url, data):
        return requests.post(self._url(url), json=data, **kwargs)

    def patch(self, url, data):
        return requests.patch(self._url(url), json=data, **kwargs)

    def delete(self, url):
        return requests.delete(self._url(url), **kwargs)

    def put(self, url, data):
        return requests.put(self._url(url), json=data, **kwargs)


client = Client()
