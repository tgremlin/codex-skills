const fs = require("fs");
const path = require("path");

function dataFileFrom(baseDir) {
  if (process.env.TODOS_FILE) {
    return process.env.TODOS_FILE;
  }
  return path.join(baseDir, "data", "todos.json");
}

function ensureArray(filePath) {
  if (!fs.existsSync(filePath)) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, "[]\n", "utf-8");
  }
  const raw = fs.readFileSync(filePath, "utf-8");
  const data = JSON.parse(raw);
  if (!Array.isArray(data)) {
    throw new Error("todos.json must contain a JSON array");
  }
  return data;
}

function writeTodos(filePath, todos) {
  fs.writeFileSync(filePath, JSON.stringify(todos, null, 2) + "\n", "utf-8");
}

function nextId(todos) {
  const max = todos.reduce((acc, item) => {
    const value = Number(item.id);
    return Number.isFinite(value) ? Math.max(acc, value) : acc;
  }, 0);
  return max + 1;
}

function createStore(baseDir) {
  const filePath = dataFileFrom(baseDir);

  function list() {
    return ensureArray(filePath);
  }

  function create(title) {
    if (typeof title !== "string" || !title.trim()) {
      throw new Error("title is required");
    }
    const todos = ensureArray(filePath);
    const todo = {
      id: nextId(todos),
      title: title.trim(),
      completed: false,
    };
    todos.push(todo);
    writeTodos(filePath, todos);
    return todo;
  }

  function update(id, patch) {
    const todoId = Number(id);
    const todos = ensureArray(filePath);
    const index = todos.findIndex((item) => Number(item.id) === todoId);
    if (index < 0) {
      return null;
    }
    const existing = todos[index];
    const next = {
      ...existing,
      title: typeof patch.title === "string" ? patch.title : existing.title,
      completed: typeof patch.completed === "boolean" ? patch.completed : existing.completed,
    };
    todos[index] = next;
    writeTodos(filePath, todos);
    return next;
  }

  function remove(id) {
    const todoId = Number(id);
    const todos = ensureArray(filePath);
    const next = todos.filter((item) => Number(item.id) !== todoId);
    if (next.length === todos.length) {
      return false;
    }
    writeTodos(filePath, next);
    return true;
  }

  return {
    filePath,
    list,
    create,
    update,
    remove,
  };
}

module.exports = {
  createStore,
};
