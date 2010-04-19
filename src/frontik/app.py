import sys
import os.path

import tornado.web
import tornado.ioloop
import logging
from tornado.options import options

log = logging.getLogger('frontik.server')        

import handler

class StatusHandler(tornado.web.RequestHandler):
    def get(self):
        self.write('pages served: %s\n' % (handler.stats.page_count,))
        self.write('http reqs made: %s\n' % (handler.stats.http_reqs_count,))


class StopHandler(tornado.web.RequestHandler):
    def get(self):
        log.info('requested shutdown')
        tornado.ioloop.IOLoop.instance().stop()


class FrontikModuleDispatcher(object):
    def __init__(self, app_dir, app_package_name='frontik_www'):
        self.app_dir = app_dir
        self.app_package_name = app_package_name
        self.app_package = self.init_app_package(app_dir, app_package_name)
        self.ph_globals = handler.PageHandlerGlobals(self.app_package)

    def init_app_package(self, app_dir, app_package_name):
        if app_dir:
            abs_app_dir = os.path.abspath(app_dir)
            log.debug('appending "%s" document_dir to sys.path', abs_app_dir)
            sys.path.insert(0, abs_app_dir)

        try:
            app_package = __import__(app_package_name)
        except:
            log.exception('%s module cannot be found', app_package_name)
            raise

        try:
            app_package.config = __import__("{0}.config".format(app_package_name), fromlist=['config'])
        except:
            log.exception('%s.config module cannot be found', app_package_name)
            raise

        if app_dir:
            if not app_package.__file__.startswith(abs_app_dir):
                msg = '%s module is found at %s while %s expected' % (
                    app_package_name, app_package.__file__, abs_app_dir)
                log.error(msg)
                raise Exception(msg)

        return app_package

    def pages_dispatcher(self, application, request):
        log.info('requested url: %s', request.uri)

        page_module_name_parts = request.path.strip('/').split('/')[1:]

        if page_module_name_parts:
            page_module_name = '{0}.pages.{1}'.format(self.app_package_name, '.'.join(page_module_name_parts))
        else:
            page_module_name = '{0}.pages'.format(self.app_package_name)

        try:
            page_module = __import__(page_module_name, fromlist=['Page'])
            log.debug('using %s from %s', page_module_name, page_module.__file__)
        except ImportError:
            log.exception('%s module not found', page_module_name)
            return tornado.web.ErrorHandler(application, request, 404)
        except:
            log.exception('error while importing %s module', page_module_name)
            return tornado.web.ErrorHandler(application, request, 500)

        try:
            return page_module.Page(self.ph_globals, application, request)
        except:
            log.exception('%s.Page class not found', page_module_name)
            return tornado.web.ErrorHandler(application, request, 500)


def get_app(pages_dispatcher):
    return tornado.web.Application([
            (r'/status/', StatusHandler),
            (r'/stop/', StopHandler),
            (r'/page/.*', pages_dispatcher),
            ])

