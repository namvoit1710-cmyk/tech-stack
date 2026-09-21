## Azure SDK for Python `_pyamqp` vendored AMQP transport

        Source: https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/servicebus/azure-servicebus/azure/servicebus/_pyamqp

        License: MIT License for Microsoft-authored Azure SDK files. The vendored
        `_transport.py` includes a BSD-3-Clause notice for code forked from
        Celery `py-amqp`; keep that notice in the file.

        Local modifications in this repository:
        - Package namespace changed to `agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp`.
        - `websocket-client` dependency removed and replaced with a local stdlib WebSocket transport.
        - `certifi` dependency removed; Python's default certificate store is used unless an SSL context is supplied.
        - `typing_extensions` imports replaced with stdlib `typing` imports for Python 3.12+.

        The vendored source-file headers refer to "License.txt in the project root".
        The corresponding MIT license text is vendored alongside the sources at
        `agent_sdk/layer4_frameworks/messaging/_vendor/azure_pyamqp/LICENSE` and is
        reproduced in full below.

### MIT License (Azure SDK for Python `_pyamqp`)

```
MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
