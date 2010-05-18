import time
import tornado.ioloop

import frontik.handler
from frontik import etree

class Page(frontik.handler.PageHandler):
    def get_page(self):
        tornado.ioloop.IOLoop.instance().add_timeout(time.time()+10, self.finish_group.add(self.async_callback(self.step2)))

    def step2(self):
        self.doc.put('ok!')