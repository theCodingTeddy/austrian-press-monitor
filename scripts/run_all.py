import setup_db
import data_parser

def main():
    """
    Runs all the scripts in order.
    """
    print("-"*35)
    print("1. Setting up database")
    print("-"*35)
    setup_db.main()

    print("-"*35)
    print("2. Parsing and cleaning data")
    print("-"*35)
    data_parser.main()

    # TODO: include all upcoming scripts

    print("-"*35)
    print("-"*35)
    print("All steps completed successfully!")
    print("-"*35)

if __name__ == "__main__":
    main()