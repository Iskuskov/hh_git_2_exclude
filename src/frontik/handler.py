# -*- coding: utf-8 -*-

from __future__ import with_statement

from functools import partial
import datetime
import functools
import functools
import httplib
import os.path
import time
import traceback
import urllib
import xml.sax.saxutils

import tornado.autoreload
import tornado.httpclient
import tornado.options
import tornado.web

from frontik import etree
import frontik.async
import frontik.auth
import frontik.doc
import frontik.http
import frontik.util

import xml_util

import logging
log = logging.getLogger('frontik.handler')
log_xsl = logging.getLogger('frontik.handler.xsl')
log_fileloader = logging.getLogger('frontik.server.fileloader')

import future

def http_header_out(*args, **kwargs):
    log_xsl.debug('x:http-header-out called')

def set_http_status(*args, **kwargs):
    log_xsl.debug('x:set-http-status called')

def x_urlencode(context, params):
    log_xsl.debug('x:urlencode called')
    if params:
        return urllib.quote(params[0].text.encode("utf8") or "")

# TODO cleanup this
ns = etree.FunctionNamespace('http://www.yandex.ru/xscript')
ns.prefix = 'x'
ns['http-header-out'] = http_header_out
ns['set-http-status'] = set_http_status
ns['urlencode'] = x_urlencode

# TODO cleanup this after release of frontik with frontik.async
AsyncGroup = frontik.async.AsyncGroup

class HTTPError(tornado.web.HTTPError):
    """An exception that will turn into an HTTP error response."""
    def __init__(self, status_code, *args, **kwargs):
        for kwarg in ["text", "xml", "xsl"]:
            setattr(self, kwarg, kwargs.setdefault(kwarg, None)) 
            del kwargs[kwarg]
        tornado.web.HTTPError.__init__(self, status_code, *args, **kwargs)


class Stats(object):
    def __init__(self):
        self.page_count = 0
        self.http_reqs_count = 0

    def next_request_id(self):
        self.page_count += 1
        return self.page_count

stats = Stats()

class PageLogger(logging.Logger):
    '''
    This class is supposed to fix huge memory 'leak' in logging
    module. I.e. every call to logging.getLogger(some_unique_name)
    wastes memory as resulting logger is memoized by
    module. PageHandler used to create unique logger on each request
    by call logging.getLogger('frontik.handler.%s' %
    (self.request_id,)). This lead to wasting about 10Mb per 10K
    requests.
    '''
    
    def __init__(self, request_id):
        logging.Logger.__init__(self, 'frontik.handler.{0}'.format(request_id))

    def handle(self, record):
        return log.handle(record)


_debug_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
class DebugPageLogger(logging.Logger):
    def __init__(self, request_id):
        logging.Logger.__init__(self, 'frontik.handler.{0}'.format(request_id))
        self.log_data = []

    def handle(self, record):
        self.log_data.append(_debug_formatter.format(record))
        return log.handle(record)


class FileCache(object):
    def __init__(self, root_dir, load_fn):
        '''
        load_fn :: filename -> (status, result)
        '''

        self.root_dir = root_dir
        self.load_fn = load_fn

        self.cache = dict()

    def load(self, filename):
        if filename in self.cache:
            log_fileloader.debug('got %s file from cache', filename)
            return self.cache[filename]
        else:
            real_filename = os.path.normpath(os.path.join(self.root_dir, filename))

            log_fileloader.debug('reading %s file from %s', filename, real_filename)
            ok, ret = self.load_fn(real_filename)

        if ok:
            self.cache[filename] = ret

        return ret


def _source_comment(src):
    return etree.Comment('Source: {0}'.format(frontik.util.asciify_url(src).replace('--', '%2D%2D')))

def xml_from_file(filename):
    ''' 
    filename -> (status, et.Element)

    status == True - результат хороший можно кешировать
           == False - результат плохой, нужно вернуть, но не кешировать
    '''

    if os.path.exists(filename):
        try:
            res = etree.parse(file(filename)).getroot()
            tornado.autoreload.watch_file(filename)

            return True, [_source_comment(filename), res]
        except:
            log.exception('failed to parse %s', filename)
            return False, etree.Element('error', dict(msg='failed to parse file: %s' % (filename,)))
    else:
        log.error('file not found: %s', filename)
        return False, etree.Element('error', dict(msg='file not found: %s' % (filename,)))


def xsl_from_file(filename):
    '''
    filename -> (True, et.XSLT)
    
    в случае ошибки выкидывает исключение
    '''

    transform, xsl_files = xml_util.read_xsl(filename)
    
    for xsl_file in xsl_files:
        tornado.autoreload.watch_file(xsl_file)

    return True, transform


class InvalidOptionCache(object):
    def __init__(self, option):
        self.option = option

    def load(self, filename):
        raise Exception('{0} option is undefined'.format(self.option))


def make_file_cache(option_name, option_value, fun):
    if option_value:
        return FileCache(option_value, fun)
    else:
        return InvalidOptionCache(option_name)


class PageHandlerGlobals(object):
    '''
    Объект с настройками для всех хендлеров
    '''
    def __init__(self, app_package):
        self.config = app_package.config

        self.xml_cache = make_file_cache('XML_root', getattr(app_package.config, 'XML_root', None), xml_from_file)
        self.xsl_cache = make_file_cache('XSL_root', getattr(app_package.config, 'XSL_root', None), xsl_from_file)

        self.http_client = frontik.http.TimeoutingHttpFetcher(
                tornado.httpclient.AsyncHTTPClient(max_clients=200, max_simultaneous_connections=200))

        
working_handlers_count = 0

class PageHandler(tornado.web.RequestHandler):
    '''
    Хендлер для конкретного запроса. Создается на каждый запрос.
    '''
    
    def __init__(self, ph_globals, application, request):
        self.handler_started = time.time()
        
        self.request_id = request.headers.get('X-Request-Id', stats.next_request_id())

        self.config = ph_globals.config
        self.xml_cache = ph_globals.xml_cache
        self.xsl_cache = ph_globals.xsl_cache
        self.http_client = ph_globals.http_client

        self.doc = frontik.doc.Doc(root_node=etree.Element('doc', frontik='true'))
        self.transform = None

        self.text = None
        self.should_dec_whc = False

        tornado.web.RequestHandler.__init__(self, application, request)

        if tornado.options.options.debug or "debug" in self.request.arguments:
            self.log = DebugPageLogger(self.request_id)
        else:
            self.log = PageLogger(self.request_id)
        self._logger = self.log

        if not tornado.options.options.debug and \
               ("debug" in self.request.arguments or "noxsl" in self.request.arguments):
            # Checks if query has `debug` or `noxsl` arguments and applies for HTTP basic auth.
            try:
                frontik.auth.require_basic_auth(self, tornado.options.options.debug_login,
                                                tornado.options.options.debug_password)
            except frontik.auth.AuthError:
                return

        self.finish_group = frontik.async.AsyncGroup(self._finish_page, log=self.log.debug)

    def _get_debug_page(self, status_code, **kwargs):
        return '<html><title>{code}</title>' \
            '<body>' \
            '<h1>{code}</h1>' \
            '<pre>{log}</pre></body>' \
            '</html>'.format(code=status_code, log='<br/>'.join(xml.sax.saxutils.escape(i).replace('\n', '<br/>').replace(' ', '&nbsp;') for i in self.log.log_data))

    def get_error_html(self, status_code, **kwargs):
        if tornado.options.options.debug:
            return self._get_debug_page(status_code, **kwargs)
        else:
            return tornado.web.RequestHandler.get_error_html(self, status_code, **kwargs)

    def send_error(self, status_code=500, **kwargs):
        def standard_send_error():
            return super(PageHandler, self).send_error(status_code, **kwargs)

        def xsl_send_error():
            return

        def plaintext_send_error():
            return
            
        exception = kwargs.get("exception", None)

        if exception:
            self.set_status(status_code)

            if getattr(exception, "text", None) is not None:
                self.set_plaintext_response(exception.text)
                return plaintext_send_error()

            if getattr(exception, "xml", None) is not None:
                self.doc.put(exception.xml)

                if getattr(exception, "xsl", None) is not None:
                    self.set_xsl(exception.xsl)
                    return xsl_send_error()
                elif self.transform:
                    return xsl_send_error()
                else:
                    return standard_send_error()
        return standard_send_error()

    # эта заляпа сливает обработчики get и post запросов
    @tornado.web.asynchronous
    def post(self, *args, **kw):
        self.get(*args, **kw)

    @tornado.web.asynchronous
    def get(self, *args, **kw):
        global working_handlers_count
        working_handlers_count += 1
        self.should_dec_whc = True

        if working_handlers_count < tornado.options.options.handlers_count:
            self.log.debug('started %s %s (workers_count = %s)',
                           self.request.method, self.request.uri, working_handlers_count)

            self.get_page()
            self.finish_page()
        else:
            self.log.warn('dropping %s %s; too many workers (%s)', self.request.method, self.request.uri, working_handlers_count)
            raise tornado.web.HTTPError(502)

    def finish(self, chunk=None):
        if self.should_dec_whc:
            global working_handlers_count
            working_handlers_count -= 1
            self.should_dec_whc = False

        tornado.web.RequestHandler.finish(self, chunk)

    def get_page(self):
        ''' Эта функция должна быть переопределена в наследнике и
        выполнять актуальную работу хендлера '''
        pass

    ###

    def async_callback(self, callback, *args, **kw):
        return tornado.web.RequestHandler.async_callback(self, self.check_finished(callback, *args, **kw))

    def check_finished(self, callback, *args, **kwargs):
        if args or kwargs:
            callback = partial(callback, *args, **kwargs)

        def wrapper(*args, **kwargs):
            if self._finished:
                self.log.warn('Page was already finished, %s ignored', callback)
            else:
                callback(*args, **kwargs)
        
        return wrapper

    ###

    def fetch_url(self, url, callback=None):
        """
        Прокси метод для get_url, логирующий употребления fetch_url
        """
        from urlparse import parse_qs, urlparse

        self.log.error("Used deprecated method `fetch_url`. %s", traceback.format_stack()[-2][:-1])
        scheme, netloc, path, params, query, fragment = urlparse(url)
        new_url = "{0}://{1}{2}".format(scheme, netloc, path)
        query = parse_qs(query)

        return self.get_url(new_url, data=query, callback=callback)

    def fetch_request(self, req, callback):
        if not self._finished:
            stats.http_reqs_count += 1

            req.headers['X-Request-Id'] = self.request_id

            return self.http_client.fetch(
                    req,
                    self.finish_group.add(self.async_callback(callback)))
        else:
            self.log.warn('attempted to make http request to %s while page is already finished; ignoring', req.url)

    def get_url(self, url, data={}, headers={}, connect_timeout=0.5, request_timeout=2, callback=None):
        placeholder = future.Placeholder()

        self.fetch_request(
            frontik.util.make_get_request(url, data, headers, connect_timeout, request_timeout),
            partial(self._fetch_request_response, placeholder, callback))

        return placeholder

    def get_url_retry(self, url, data={}, headers={}, retry_count=3, retry_delay=0.1, connect_timeout=0.5, request_timeout=2, callback=None):
        placeholder = future.Placeholder()

        req = frontik.util.make_get_request(url, data, headers, connect_timeout, request_timeout)

        def step1(retry_count, response):
            if response.error and retry_count > 0:
                self.log.warn('failed to get %s; retries left = %s; retrying', response.effective_url, retry_count)
                # TODO use handler-specific ioloop
                if retry_delay > 0:
                    tornado.ioloop.IOLoop.instance().add_timeout(time.time() + retry_delay,
                        self.finish_group.add(self.async_callback(partial(step2, retry_count))))
                else:
                    step2(retry_count)
            else:
                if response.error and retry_count == 0:
                    self.log.warn('failed to get %s; no more retries left; give up retrying', response.effective_url)

                self._fetch_request_response(placeholder, callback, response)

        def step2(retry_count):
            self.http_client.fetch(req, self.finish_group.add(self.async_callback(partial(step1, retry_count - 1))))

        self.http_client.fetch(req, self.finish_group.add(self.async_callback(partial(step1, retry_count - 1))))
        
        return placeholder

    def post_url(self, url, data={},
                 headers={},
                 files={},
                 connect_timeout=0.5, request_timeout=2,
                 callback=None):
        
        placeholder = future.Placeholder()
        
        self.fetch_request(
            frontik.util.make_post_request(url, data, headers, files, connect_timeout, request_timeout),
            partial(self._fetch_request_response, placeholder, callback))
        
        return placeholder

    def _parse_response(self, response):
        '''
        return :: (placeholder_data, response_as_xml)
        None - в случае ошибки парсинга
        '''

        if response.error:
            self.log.warn('%s failed %s (%s)', response.code, response.effective_url, str(response.error))
            data = [etree.Element('error', dict(url=response.effective_url, reason=str(response.error), code=str(response.code)))]

            if response.body:
                try:
                    data.append(etree.Comment(response.body.replace("--", "%2D%2D")))
                except ValueError:
                    self.log.warn("Could not add debug info in XML comment with unparseable response.body. non-ASCII response.")
                    
            return (data, None)
        else:
            try:
                element = etree.fromstring(response.body)
            except:
                if len(response.body) > 100:
                    body_preview = '{0}...'.format(response.body[:100])
                else:
                    body_preview = response.body

                self.log.warn('failed to parse XML response from %s data "%s"',
                                 response.effective_url,
                                 body_preview)

                return (etree.Element('error', dict(url=response.effective_url, reason='invalid XML')),
                        None)

            else:
                return ([_source_comment(response.effective_url), element],
                        element)

    def _fetch_request_response(self, placeholder, callback, response):
        self.log.debug('got %s %s in %.2fms', response.code, response.effective_url, response.request_time*1000)
        
        data, xml = self._parse_response(response)
        placeholder.set_data(data)

        if callback:
            callback(xml, response)

    ###

    def set_plaintext_response(self, text):
        self.text = text

    ###

    def finish_page(self):
        self.finish_group.try_finish()

    def _finish_page(self):        
        if not self._finished:
            res = None
            
            if "debug" in self.request.arguments:
                res = self._prepare_finish_debug_mode()
            elif self.text is not None:
                res = self._prepare_finish_plaintext()
            elif self.transform:
                res = self._prepare_finish_with_xsl()
            else:
                res = self._prepare_finish_wo_xsl()
            
            self.postprocessor_started = None
            if hasattr(self.config, 'postprocessor'):
                self.postprocessor_started = time.time()
                self.config.postprocessor(res, self, self._end_finish_page)
            else:
                self._end_finish_page(res)

        else:
            self.log.warn('trying to finish already finished page, probably bug in a workflow, ignoring')

    def _end_finish_page(self, data):
        if self.postprocessor_started:
            self.log.debug("applied postprocessor '%s' in %.2fms",
                    self.config.postprocessor,
                    (time.time() - self.postprocessor_started)*1000)
        self.finish(data)
        self.log.debug('done in %.2fms', (time.time() - self.handler_started)*1000)

    def _prepare_finish_debug_mode(self):
        self.set_header('Content-Type', 'text/html')
        return self._get_debug_page(self._status_code)

    def _prepare_finish_with_xsl(self):
        self.log.debug('finishing with xsl')

        if not self._headers.get("Content-Type", None):
            self.set_header('Content-Type', 'text/html')

        try:
            t = time.time()
            result = str(self.transform(self.doc.to_etree_element()))
            self.log.debug('applied XSL %s in %.2fms', self.transform_filename, (time.time() - t)*1000)
            return result           
        except:
            self.log.exception('failed transformation with XSL %s' % self.transform_filename)
            raise

    def _prepare_finish_wo_xsl(self):
        self.log.debug('finishing wo xsl')

        if not self._headers.get("Content-Type", None):
            self.set_header('Content-Type', 'application/xml')

        return self.doc.to_string()
       
    def _prepare_finish_plaintext(self):
        self.log.debug("finishing plaintext")
        return self.text

    ###

    def xml_from_file(self, filename):
        return self.xml_cache.load(filename)

    def _set_xsl_log_and_raise(self, msg_template):
        msg = msg_template.format(self.transform_filename)
        self.log.exception(msg)
        raise tornado.web.HTTPError(500, msg)

    def set_xsl(self, filename):
        if not self.config.apply_xsl:
            self.log.debug('ignored set_xsl(%s) because config.apply_xsl=%s', filename, self.config.apply_xsl)        
            return

        if self.get_argument('noxsl', None):
            self.log.debug('ignored set_xsl(%s) because noxsl=%s', filename, self.get_argument('noxsl'))
            return
                           
        self.transform_filename = filename

        try:
            self.transform = self.xsl_cache.load(filename)

        except etree.XMLSyntaxError, error:
            self._set_xsl_log_and_raise('failed parsing XSL file {0} (XML syntax)')
        except etree.XSLTParseError, error:
            self._set_xsl_log_and_raise('failed parsing XSL file {0} (dumb xsl)')
        except:
            self._set_xsl_log_and_raise('XSL transformation error with file {0}')
