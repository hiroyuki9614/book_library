import { Link } from "react-router-dom";

function NotFound() {
  return (
    <>
      <h1>404 Not Found</h1>
      <Link to="/">back</Link>
    </>
  );
}

export default NotFound;
