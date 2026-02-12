const http = require("http");
const path = require("path");

const { createStore } = require("./lib/todo_store");

const port = Number(process.env.PORT || 3210);
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

function html(res, code, payload) {
  res.writeHead(code, { "Content-Type": "text/html; charset=utf-8" });
  res.end(payload);
}

const appHtml = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Todo Baseline</title>
    <style>
      body { font-family: sans-serif; margin: 2rem; }
      form { display: flex; gap: 0.5rem; }
      ul { margin-top: 1rem; }
      button { cursor: pointer; }
    </style>
  </head>
  <body>
    <h1>Todo Baseline</h1>
    <form id="todo-form">
      <input id="title" placeholder="Add a todo" required />
      <button type="submit">Add</button>
    </form>
    <ul id="todos"></ul>
    <script>
      async function loadTodos() {
        const res = await fetch('/api/todos');
        const todos = await res.json();
        const list = document.getElementById('todos');
        list.innerHTML = '';
        for (const todo of todos) {
          const li = document.createElement('li');
          li.textContent = todo.completed ? '[x] ' + todo.title : '[ ] ' + todo.title;
          const toggle = document.createElement('button');
          toggle.textContent = 'toggle';
          toggle.onclick = async () => {
            await fetch('/api/todos/' + todo.id, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ completed: !todo.completed, title: todo.title })
            });
            loadTodos();
          };
          const remove = document.createElement('button');
          remove.textContent = 'delete';
          remove.onclick = async () => {
            await fetch('/api/todos/' + todo.id, { method: 'DELETE' });
            loadTodos();
          };
          li.append(' ', toggle, ' ', remove);
          list.append(li);
        }
      }

      document.getElementById('todo-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const input = document.getElementById('title');
        await fetch('/api/todos', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: input.value })
        });
        input.value = '';
        loadTodos();
      });

      loadTodos();
    </script>
  </body>
</html>`;

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (req.method === "GET" && url.pathname === "/") {
      html(res, 200, appHtml);
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
