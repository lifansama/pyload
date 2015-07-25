# -*- coding: utf-8 -*-

from module.plugins.internal.Account import Account


class FourSharedCom(Account):
    __name__    = "FourSharedCom"
    __type__    = "account"
    __version__ = "0.06"
    __status__  = "testing"

    __description__ = """FourShared.com account plugin"""
    __license__     = "GPLv3"
    __authors__     = [("zoidberg", "zoidberg@mujmail.cz"),
                       ("stickell", "l.stickell@yahoo.it")]


    def parse_info(self, user, password, data, req):
        #: Free mode only for now
        return {'premium': False}


    def login(self, user, password, data, req):
        req.cj.setCookie("4shared.com", "4langcookie", "en")

        res = self.load("https://www.4shared.com/web/login",
                        post={'login'    : user,
                              'password' : password,
                              'remember' : "on",
                              '_remember': "on",
                              'returnTo' : "http://www.4shared.com/account/home.jsp"})

        if 'Please log in to access your 4shared account' in res:
            self.login_fail()
