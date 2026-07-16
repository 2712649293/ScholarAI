.PHONY: init-env up-be up-fe test-be test-fe install-be install-fe clean

# 从 apikey.txt 生成 backend/.env（只读不写 apikey 内容到任何 git 跟踪文件）
init-env:
	@if [ ! -f backend/.env ]; then \
		cp .env.example backend/.env && \
		KEY=$$(awk -F: '/^apikey:/{print $$2}' apikey.txt | tr -d ' \r\n') && \
		MODEL=$$(awk -F: '/^model:/{print $$2}' apikey.txt | tr -d ' \r\n') && \
		sed -i "s|^LLM_API_KEY=.*|LLM_API_KEY=$$KEY|" backend/.env && \
		sed -i "s|^LLM_MODEL=.*|LLM_MODEL=$$MODEL|" backend/.env && \
		echo "✓ backend/.env created from apikey.txt"; \
	else \
		echo "✓ backend/.env already exists, skip"; \
	fi

install-be:
	cd backend && uv sync

install-fe:
	cd frontend && npm install

up-be:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

up-fe:
	cd frontend && npm run dev

test-be:
	cd backend && uv run pytest -v

test-fe:
	cd frontend && npm test

clean:
	rm -rf backend/.venv frontend/node_modules data/
