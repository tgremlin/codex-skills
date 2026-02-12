const fs = require("fs");
const os = require("os");
const path = require("path");

const { createStore } = require("../lib/todo_store");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function main() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "todo-store-"));
  const filePath = path.join(tempDir, "todos.json");
  process.env.TODOS_FILE = filePath;

  const store = createStore(path.join(__dirname, ".."));

  const created = store.create("write tests");
  assert(created.id === 1, "expected first id to be 1");

  const listed = store.list();
  assert(Array.isArray(listed) && listed.length === 1, "expected one todo after create");

  const updated = store.update(created.id, { completed: true, title: "updated" });
  assert(updated && updated.completed === true, "expected update to set completed=true");

  const removed = store.remove(created.id);
  assert(removed === true, "expected remove to return true");

  const final = store.list();
  assert(final.length === 0, "expected empty list after delete");

  console.log(JSON.stringify({ ok: true, checks: 5 }));
}

try {
  main();
} catch (err) {
  console.error(err.message || String(err));
  process.exit(1);
}
