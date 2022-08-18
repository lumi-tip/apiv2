"""
Requests mock
"""
from unittest.mock import Mock
from .response_mock import ResponseMock


def request_mock(endpoints=[]):

    def base(url: str, *args, **kwargs):
        """Requests get mock"""
        if (url == 'GET' or url == 'POST' or url == 'PUT' or url == 'PATCH' or url == 'DELETE'
                or url == 'HEAD' or url == 'REQUEST'):
            url = args[0]

        match = [(status, data) for (status, endpoint, data) in endpoints if url == endpoint]

        if match:
            (status, data) = match[0]
            return ResponseMock(data=data, status_code=status, url=url)

        return ResponseMock(data='not fount', status_code=404)

    return Mock(side_effect=base)
