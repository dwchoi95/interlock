# The real-server scenarios of Section 5.3: MCPWild's image with the four pinned servers preinstalled, so the code a
# scenario runs is the version Summarize read and no package is fetched at run time.
FROM nikolaik/python-nodejs:python3.12-nodejs22
RUN pip install --no-cache-dir uv \
 && npm install -g @antv/mcp-server-chart@0.9.10 @arizeai/phoenix-mcp@4.3.13 @modelcontextprotocol/server-filesystem@2026.8.31 \
 && uv tool install mcp-server-fetch==2026.8.18
ENV PATH="/root/.local/bin:${PATH}"
