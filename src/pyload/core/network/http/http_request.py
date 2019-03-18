# -*- coding: utf-8 -*-
# AUTHOR: RaNaN

import codecs
import io

from http.client import responses
from itertools import chain
from logging import getLogger
from urllib.parse import quote, urlencode

import pycurl
from ..exceptions import Abort

from pyload import APPID


def myquote(url):
    try:
        url = url.encode()
    except AttributeError:
        pass
    return quote(url, safe="%/:=&?~#+!$,;'@()*[]")


def myurlencode(data):
    data = dict(data)
    return urlencode(
        {
            x.encode()
            if hasattr(x, "encode")
            else x: y.encode()
            if hasattr(y, "encode")
            else y
            for x, y in data.items()
        }
    )


BAD_STATUS_CODES = tuple(
    chain((400,), (401,), range(403, 406), range(408, 418), range(500, 506))
)

PROPRIETARY_RESPONSES = {
    440: "Login Timeout - The client's session has expired and must log in again.",
    449: "Retry With - The server cannot honour the request because the user has not provided the required information",
    451: "Redirect - Unsupported Redirect Header",
    509: "Bandwidth Limit Exceeded",
    520: "Unknown Error",
    521: "Web Server Is Down - The origin server has refused the connection from CloudFlare",
    522: "Connection Timed Out - CloudFlare could not negotiate a TCP handshake with the origin server",
    523: "Origin Is Unreachable - CloudFlare could not reach the origin server",
    524: "A Timeout Occurred - CloudFlare did not receive a timely HTTP response",
    525: "SSL Handshake Failed - CloudFlare could not negotiate a SSL/TLS handshake with the origin server",
    526: "Invalid SSL Certificate - CloudFlare could not validate the SSL/TLS certificate that the origin server presented",
    527: "Railgun Error - CloudFlare requests timeout or failed after the WAN connection has been established",
    530: "Site Is Frozen - Used by the Pantheon web platform to indicate a site that has been frozen due to inactivity",
}


class BadHeader(Exception):
    def __init__(self, code, header=b"", content=b""):
        int_code = int(code)
        response = responses.get(
            int_code, PROPRIETARY_RESPONSES.get(int_code, "unknown error code")
        )
        super().__init__(f"Bad server response: {code} {response}")
        self.code = int_code
        self.header = header
        self.content = content


class HTTPRequest:
    def __init__(self, cookies=None, options=None):
        self.c = pycurl.Curl()
        self.rep = None

        self.cj = cookies  #: cookiejar

        self.lastURL = None
        self.lastEffectiveURL = None
        self.abort = False
        self.code = 0  #: last http code

        self.header = bytes()

        self.headers = []  #: temporary request header

        self.initHandle()
        self.setInterface(options)

        self.c.setopt(pycurl.WRITEFUNCTION, self.write)
        self.c.setopt(pycurl.HEADERFUNCTION, self.writeHeader)

        self.log = getLogger(APPID)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initHandle(self):
        """
        sets common options to curl handle.
        """
        self.c.setopt(pycurl.FOLLOWLOCATION, 1)
        self.c.setopt(pycurl.MAXREDIRS, 10)
        self.c.setopt(pycurl.CONNECTTIMEOUT, 30)
        self.c.setopt(pycurl.NOSIGNAL, 1)
        self.c.setopt(pycurl.NOPROGRESS, 1)
        if hasattr(pycurl, "AUTOREFERER"):
            self.c.setopt(pycurl.AUTOREFERER, 1)
        self.c.setopt(pycurl.SSL_VERIFYPEER, 0)
        self.c.setopt(pycurl.LOW_SPEED_TIME, 60)
        self.c.setopt(pycurl.LOW_SPEED_LIMIT, 5)
        if hasattr(pycurl, "USE_SSL"):
            self.c.setopt(pycurl.USE_SSL, pycurl.USESSL_TRY)

        # self.c.setopt(pycurl.VERBOSE, 1)

        self.c.setopt(
            pycurl.USERAGENT,
            b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.113 Safari/537.36",
        )
        if pycurl.version_info()[7]:
            self.c.setopt(pycurl.ENCODING, b"gzip, deflate")
        self.c.setopt(
            pycurl.HTTPHEADER,
            [
                b"Accept: */*",
                b"Accept-Language: en-US,en",
                b"Accept-Charset: ISO-8859-1,utf-8;q=0.7,*;q=0.7",
                b"Connection: keep-alive",
                b"Keep-Alive: 300",
                b"Expect:",
            ],
        )

    def setInterface(self, options):
        options = {k: v.encode() if hasattr(v, "encode") else v for k, v in options.items()}
        
        interface, proxy, ipv6 = (
            options["interface"],
            options["proxies"],
            options["ipv6"],
        )

        if interface and interface.lower() != "none":
            self.c.setopt(pycurl.INTERFACE, interface)

        if proxy:
            if proxy["type"] == "socks4":
                self.c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS4)
            elif proxy["type"] == "socks5":
                self.c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5)
            else:
                self.c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_HTTP)

            self.c.setopt(pycurl.PROXY, proxy["host"])
            self.c.setopt(pycurl.PROXYPORT, int(proxy["port"]))

            if proxy["username"]:
                user = proxy["username"]
                pw = proxy["password"]
                self.c.setopt(pycurl.PROXYUSERPWD, f"{user}:{pw}".encode())

        if ipv6:
            self.c.setopt(pycurl.IPRESOLVE, pycurl.IPRESOLVE_WHATEVER)
        else:
            self.c.setopt(pycurl.IPRESOLVE, pycurl.IPRESOLVE_V4)

        if "auth" in options:
            self.c.setopt(pycurl.USERPWD, options["auth"])

        if "timeout" in options:
            self.c.setopt(pycurl.LOW_SPEED_TIME, int(options["timeout"]))

    def addCookies(self):
        """
        put cookies from curl handle to cj.
        """
        if self.cj:
            self.cj.addCookies(self.c.getinfo(pycurl.INFO_COOKIELIST))

    def getCookies(self):
        """
        add cookies from cj to curl handle.
        """
        if self.cj:
            for c in self.cj.getCookies():
                self.c.setopt(pycurl.COOKIELIST, c)
        return

    def clearCookies(self):
        self.c.setopt(pycurl.COOKIELIST, "")

    def setRequestContext(self, url, get, post, referer, cookies, multipart=False):
        """
        sets everything needed for the request.
        """
        self.rep = io.BytesIO()

        url = myquote(url)

        if get:
            get = urlencode(get)
            url = f"{url}?{get}"

        self.c.setopt(pycurl.URL, url)
        self.c.lastUrl = url

        if post:
            self.c.setopt(pycurl.POST, 1)
            if not multipart:
                if isinstance(post, str):
                    post = post.encode()
                else:  # TODO: check if mapping
                    post = myurlencode(post)

                self.c.setopt(pycurl.POSTFIELDS, post)
            else:
                post = [
                    (x, y.encode() if hasattr(y, "encode") else y)
                    for x, y in post.items()
                ]
                self.c.setopt(pycurl.HTTPPOST, post)
        else:
            self.c.setopt(pycurl.POST, 0)

        if referer and self.lastURL:
            self.c.setopt(pycurl.REFERER, self.lastURL)

        if cookies:
            self.c.setopt(pycurl.COOKIEFILE, b"")
            self.c.setopt(pycurl.COOKIEJAR, b"")
            self.getCookies()

    def load(
        self,
        url,
        get={},
        post={},
        referer=True,
        cookies=True,
        just_header=False,
        multipart=False,
        decode=False,
        follow_location=True, 
        save_cookies=True
    ):
        """
        load and returns a given page.
        """
        self.setRequestContext(url, get, post, referer, cookies, multipart)

        self.header = bytes()

        self.c.setopt(pycurl.HTTPHEADER, self.headers)

        if post:
            self.c.setopt(pycurl.POST, 1)
        else:
            self.c.setopt(pycurl.HTTPGET, 1)

        if not follow_location:
            self.c.setopt(pycurl.FOLLOWLOCATION, 0)

        if just_header:
            self.c.setopt(pycurl.NOBODY, 1)
            
        self.c.perform()
        rep = self.header if just_header else self.getResponse()

        if not follow_location:
            self.c.setopt(pycurl.FOLLOWLOCATION, 1)

        if just_header:
            self.c.setopt(pycurl.NOBODY, 0)

        self.c.setopt(pycurl.POSTFIELDS, b"")
        self.lastEffectiveURL = self.c.getinfo(pycurl.EFFECTIVE_URL)

        if save_cookies:
            self.addCookies()
            
        try:
            self.code = self.verifyHeader()

        finally:
            self.rep.close()
            self.rep = None
                        
        if decode:
            rep = self.decodeResponse(rep)

        return rep

    def verifyHeader(self):
        """
        raise an exceptions on bad headers.
        """
        code = int(self.c.getinfo(pycurl.RESPONSE_CODE))
        if code in BAD_STATUS_CODES:
            # 404 will NOT raise an exception
            raise BadHeader(code, self.header, self.getResponse())
        return code

    def checkHeader(self):
        """
        check if header indicates failure.
        """
        return int(self.c.getinfo(pycurl.RESPONSE_CODE)) not in BAD_STATUS_CODES

    def getResponse(self):
        """
        retrieve response from bytes io.
        """
        if self.rep is None:
            return ""
        else:
            value = self.rep.getvalue()
            self.rep.close()
            self.rep = io.BytesIO()
            return value

    def decodeResponse(self, rep):
        """
        decode with correct encoding, relies on header.
        """
        header = self.header.splitlines()
        encoding = "utf-8"  #: default encoding

        for line in header:
            line = line.lower().replace(" ", "")
            if not line.startswith("content-type:") or (
                "text" not in line and "application" not in line
            ):
                continue

            none, delemiter, charset = line.rpartition("charset=")
            if delemiter:
                charset = charset.split(";")
                if charset:
                    encoding = charset[0]

        try:
            # self.log.debug(f"Decoded {encoding}")
            if codecs.lookup(encoding).name == "utf-8" and rep.startswith(
                codecs.BOM_UTF8
            ):
                encoding = "utf-8-sig"

            decoder = codecs.getincrementaldecoder(encoding)("replace")
            rep = decoder.decode(rep, True)

            # TODO: html_unescape as default

        except LookupError:
            self.log.debug(f"No Decoder foung for {encoding}")

        except Exception:
            self.log.debug(f"Error when decoding string from {encoding}", exc_info=True)

        return rep

    def write(self, buf):
        """
        writes response.
        """
        if self.rep.tell() > 1_000_000 or self.abort:
            rep = self.getResponse()
            if self.abort:
                raise Abort
            with open("response.dump", mode="wb") as file:
                file.write(rep)
            raise Exception("Loaded Url exceeded limit")

        self.rep.write(buf)

    def writeHeader(self, buf):
        """
        writes header.
        """
        self.header += buf

    def putHeader(self, name, value):
        self.headers.append(f"{name}: {value}")

    def clearHeaders(self):
        self.headers = []

    def close(self):
        """
        cleanup, unusable after this.
        """
        if self.rep:
            self.rep.close()
            del self.rep

        if hasattr(self, "cj"):
            del self.cj

        if hasattr(self, "c"):
            self.c.close()
            del self.c