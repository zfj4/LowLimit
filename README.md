# LowLimit

**Low Limit Sports Betting Application** — A single-page app for placing virtual-dollar wagers on NCAA basketball games, with a $10 weekly deposit cap. Built with Django, HTMX, Tailwind CSS, and PostgreSQL.

---

## Deployment & Setup

Follow these steps to get LowLimit running locally.

### 1. Download and Extract
* Click **Code → Download ZIP** to download the repository.
* Extract the ZIP file to your working directory (e.g., `C:\GitHub\LowLimit`).

### 2. Environment & Dependencies
Open your terminal, navigate to the project root folder and run:
* **Create Virtual Environment:** `python -m venv venv`
* **Activate Environment:**
  * **Windows:** `.\venv\Scripts\activate`
    * *(If scripts are blocked, run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` first, then re-activate.)*
  * **Mac/Linux:** `source venv/bin/activate`
* **Install Requirements:** `pip install -r dependencies.txt`

### 3. Configure Environment Variables
* Copy `.env.example` to `.env`: `cp .env.example .env`
* Open `.env` and set your **Google API key** (required for AI-generated game lists):
  ```
  GOOGLE_API_KEY=your-google-api-key-here
  ```
* The other values can stay as-is for local development.

### 4. Database & Docker
The database runs in a Docker container to ensure a consistent environment.
* Download and install **Docker Desktop** from [docker.com](https://www.docker.com/).
* Ensure **Docker Desktop** is installed and running, then start the PostgreSQL container:
  ```
  docker-compose up -d
  ```

### 5. Database Initialization
Run migrations to build the table structure, then create your admin account:
```
python manage.py migrate
python manage.py createsuperuser
```

### 6. Run the Application
* **Start Server:** `python manage.py runserver 8080`
* **Access App:** Go to `http://127.0.0.1:8080`

### 7. Run Tests
```
pytest
```

---

## Admin — Settling Wagers

To settle wagers after a game is played:
1. Go to `http://127.0.0.1:8080/admin/` and log in with your superuser credentials.
2. Under **Sporting Events**, find the game and enter the final scores in the **Home score** and **Away score** fields.
3. Set **Status** to `Final` and save.
4. All pending wagers on that game are automatically settled and balances updated.
