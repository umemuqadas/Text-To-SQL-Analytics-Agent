# Text-to-SQL Analytics Agent

**AI Engineering · Generative AI · Agentic AI · Database Analytics**

Text-to-SQL Analytics Agent is an AI-powered tool that allows users to ask questions about their database in simple English.

Instead of writing SQL queries manually, users can ask questions like:

- What are the total sales?
- Which product has the highest sales?
- Show monthly revenue.
- How many orders were placed in 2026?

The AI converts these questions into SQL queries, executes them safely, and displays the results in tables, charts, and simple explanations.

## How It Works

The project uses multiple AI agents and tools to process questions.

1. **Schema Agent:** Understands the database and finds the required tables and columns.
2. **SQL Generator:** Converts the user's question into an SQL query using an AI model.
3. **SQL Validator:** Checks whether the query is safe and follows the rules.
4. **Query Executor:** Runs the approved query on the database.
5. **Result Analyzer:** Explains the results in simple language.
6. **Visualization Agent:** Creates charts and tables to make results easier to understand.

If a query has an error, the system can try to fix it up to 3 times.

## Features

- Ask database questions using normal English.
- Automatically generate SQL queries.
- Validate SQL queries before execution.
- Read-only database access for better safety.
- Display results in tables and charts.
- Generate AI-powered summaries and insights.
- Upload CSV, Excel, SQLite, and SQL files.
- Connect PostgreSQL and MySQL databases.
- View previous questions and results.
- Export results to CSV and Excel.
- Works without a paid AI API key using a local model.
- Supports offline rule-based SQL generation when Ollama is unavailable.

## Tech Stack

| Component | Technology |
|---|---|
| Frontend | React, Vite, Tailwind CSS |
| Backend | Python, FastAPI |
| Database | PostgreSQL, SQLite |
| ORM | SQLAlchemy |
| AI Model | Ollama, Qwen2.5-Coder |
| Agent Framework | LangGraph |
| SQL Validation | SQLGlot |
| Charts | Plotly |
| API | REST API |

## Getting Started

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd text-to-sql-agent
```

### 2. Setup Database

For PostgreSQL using Docker:

```bash
docker compose up -d db
```

Or use the SQLite demo database:

```bash
cd backend
python scripts/init_demo_db.py
```

### 3. Setup Backend

```bash
cd backend

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt

copy .env.example .env

uvicorn app.main:app --reload --port 8000
```

Backend API documentation:

http://localhost:8000/docs

### 4. Setup AI Model

Install Ollama and run:

```bash
ollama pull qwen2.5-coder:7b
ollama serve
```

The model runs locally, so no paid API key is required.

### 5. Setup Frontend

Open another terminal:

```bash
cd frontend

npm install
npm run dev
```

Open the application:

http://localhost:5173

## Database Support

You can use your own data instead of the sample database.

Supported sources:

- SQLite database files
- CSV files
- Excel files
- SQL database dumps
- PostgreSQL databases
- MySQL databases

Simply upload a file or connect a database through the Sources page.

## Security

Database safety is an important part of this project.

The system includes:

- Only SELECT queries are allowed.
- INSERT, UPDATE, DELETE, and DROP queries are blocked.
- Only approved database tables can be accessed.
- Sensitive columns can be hidden.
- Query results have a maximum row limit.
- Queries have execution time limits.
- Database connections use read-only access.
- Uploaded files are checked before processing.

AI-generated SQL is never executed without passing the validation checks.

## Testing

Run the backend tests:

```bash
cd backend
pytest ../tests -v
```

Tests cover SQL validation, database execution, AI agents, file uploads, database connections, and offline query generation.

## Project Structure

```text
text-to-sql-agent/
│
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   ├── database/
│   │   ├── services/
│   │   ├── graph/
│   │   ├── api/
│   │   └── main.py
│   │
│   ├── scripts/
│   └── data/
│
├── frontend/
│   └── src/
│       ├── components/
│       ├── pages/
│       └── services/
│
├── database/
│   ├── schema.sql
│   └── sample_data.sql
│
├── tests/
└── docker-compose.yml
```

## Project Goals

This project was built to explore:

- Generative AI and LLMs
- AI Agents and Agentic AI
- Natural Language to SQL
- Database interaction
- Retrieval of database schemas
- SQL validation and security
- Data analysis and visualization
- Building AI-powered applications

**Main Idea:** Ask questions in simple English, let AI write the SQL, and get meaningful answers from your database.
