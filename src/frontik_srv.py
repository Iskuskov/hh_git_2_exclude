#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import os.path
import tornado.options
from tornado.options import options
import tornado_util.server
import logging

log = logging.getLogger('frontik')

if __name__ == '__main__':
    dev_config = os.path.join(os.path.dirname(__file__), 'frontik_dev.cfg')
    if os.path.exists(dev_config):
        config = dev_config
    else:
        config = '/etc/frontik/frontik.cfg'

    tornado.options.define('document_root', None, str)
    tornado.options.define('use_standart_doc', True, bool)
    tornado.options.define('app_package', 'frontik_www', str)
    tornado.options.define('app_config', '', str)
    tornado.options.define('suppressed_loggers', ['tornado.httpclient'], list)

    tornado_util.server.bootstrap(config)

    import frontik.app
    app_package = frontik.app.init_app_package(options.document_root, options.app_package)

    for log_channel_name in options.suppressed_loggers:
        logging.getLogger(log_channel_name).setLevel(logging.WARN)

    tornado_util.server.main(frontik.app.get_app(app_package))

