#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import os.path

import logging
import webob.exc
import ConfigParser

log = logging.getLogger('frontik.server')

class FrontikApp(object):
    def __init__(self):
        pass
    
    def __call__(self, environ, start_response):
        req = webob.Request(environ)
        log.info('requested url: %s', req.url)
        
        page_module_name = 'frontik_www.pages.' + req.path_info.strip('/').replace('/', '.')
        
        try:
            try:
                page_module = __import__(page_module_name, fromlist=['get_page'])
                log.debug('using %s from %s', page_module_name, page_module.__file__)
            except:
                raise webob.exc.HTTPNotFound('%s module not found' % (page_module_name,))
            
            try:
                page_handler = page_module.get_page
            except:
                raise webob.exc.HTTPNotFound('%s.get_page method not found' % (page_module_name,))
            
            return page_handler(req)(environ, start_response)
        except webob.exc.HTTPException, e:
            return e(environ, start_response)

if __name__ == '__main__':
    app = FrontikApp()

    logging.basicConfig(level=logging.DEBUG)
    
    cp = ConfigParser.ConfigParser()
    configs = cp.read(['/etc/frontik/frontik.ini', './frontik.dev.ini'])
    
    if configs:
        log.debug('read configs: %s', ', '.join(os.path.abspath(i) for i in configs))
    else:
        log.error('failed to find any config file, aborting')
        sys.exit(1)
    
    special_document_dir = os.path.abspath(cp.get('server', 'document_dir'))
    if special_document_dir:
        log.debug('appending "%s" document_dir to sys.path', special_document_dir)
        sys.path.append(special_document_dir)
    
    if len(sys.argv) > 1:
        request = webob.Request.blank(sys.argv[1])
        print ''.join(app(request.environ, lambda *args, **kw: None))
    
    else:
        from paste import httpserver
        
        host=cp.get('server', 'host')
        port=cp.getint('server', 'port')
        log.debug('binding to %s:%s', host, port)
        
        httpserver.serve(app, host=host, port=port)
