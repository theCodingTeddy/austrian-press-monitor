import setup_db
import data_parser

def main():
    """
    Runs all the scripts in order.
    """
    print("1/2 Setting up database...")
    setup_db.main()

    print("2/2 Parsing and cleaning data...")
    data_parser.main()

    # TODO: include all upcoming scripts

    print("-"*32)
    print("All steps completed successfully!")

if __name__ == "__main__":
    main()