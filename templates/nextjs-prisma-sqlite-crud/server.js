const http = require("http");
const { createStore } = require("./lib/todo_store");

const port = Number(process.env.PORT || 3211);
const store = createStore(__dirname);

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk.toString();
    });
    req.on("end", () => {
      if (!body) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(body));
      } catch (err) {
        reject(new Error("Invalid JSON payload"));
      }
    });
    req.on("error", (err) => reject(err));
  });
}

function json(res, code, payload) {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<h1>nextjs-prisma-sqlite-crud</h1>");
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/health") {
      json(res, 200, { ok: true });
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/todos") {
      json(res, 200, store.list());
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/todos") {
      const payload = await readBody(req);
      const created = store.create(payload.title);
      json(res, 201, created);
      return;
    }

    if (req.method === "PUT" && url.pathname.startsWith("/api/todos/")) {
      const id = Number(url.pathname.split("/").pop());
      const payload = await readBody(req);
      const updated = store.update(id, payload);
      if (!updated) {
        json(res, 404, { error: "todo not found" });
        return;
      }
      json(res, 200, updated);
      return;
    }

    if (req.method === "DELETE" && url.pathname.startsWith("/api/todos/")) {
      const id = Number(url.pathname.split("/").pop());
      const removed = store.remove(id);
      if (!removed) {
        json(res, 404, { error: "todo not found" });
        return;
      }
      json(res, 200, { deleted: id });
      return;
    }

    json(res, 404, { error: "not found" });
  } catch (err) {
    json(res, 500, { error: err.message || "unexpected error" });
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`server running at http://127.0.0.1:${port}`);
});
