from fastmcp import FastMCP

mcp = FastMCP("drillbit-mcp")


@mcp.tool()
def ping() -> str:
    return "Hello world!"


if __name__ == "__main__":
    mcp.run(transport="sse")
