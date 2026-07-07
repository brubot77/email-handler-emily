from app.active_deals_sheets import ensure_active_deals_tabs_only

def main():
    created = ensure_active_deals_tabs_only()
    print("Created missing native Google Sheets tabs:", created)

if __name__ == "__main__":
    main()