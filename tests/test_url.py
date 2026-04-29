import urllib.request
url = "https://news.google.com/rss/articles/CBMirwFBVV95cUxNVHdzZ0dROXQxbEt3WnJrd0hib2Y2eVRBWFJ1dk1sckY2anREUHVVYzhGSUtVdGs0VTBUc2lwb2Zfb2l6bWxsTzJzcXAtT0ZTSU5Yc0JabDAtZG1Jb21mTWx5Qkdtb2VHcHdCU0RrZUZxa2JfVkx0UDJiSm9CTHFLMFFsUDVpWmxYb3hmeTQ0WTVZc0hoc3B0a1J2MjJORG5fVHZ6UFA2Z0I5TlIzUUNR?oc=5"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req)
    print("Final URL:", response.url)
except Exception as e:
    print("Error:", e)
