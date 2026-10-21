import unittest
from zfly.proxy_model import group_names

class ProxyOrderingTests(unittest.TestCase):
    def test_order_visibility_and_cross_group_search(self):
        proxies = {'GLOBAL': {'all': ['Z', 'hidden', 'A', 'DIRECT']},
                   'A': {'all': ['one']}, 'Z': {'all': ['two']},
                   'hidden': {'all': ['one'], 'hidden': True}}
        self.assertEqual(group_names(proxies), ['Z', 'A'])
        self.assertEqual(group_names(proxies, 'global'), ['Z', 'A', 'GLOBAL'])
        self.assertEqual(group_names(proxies, query='two'), ['Z'])
        self.assertEqual(group_names(proxies, query='missing'), [])
