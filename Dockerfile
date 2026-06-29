FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 8000

# Run the MCP server over streamable HTTP
CMD ["combine-mcp", "serve", "--transport", "streamable-http", "--port", "8000"]
