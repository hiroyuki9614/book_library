import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';

import { db } from '@/features/funcs/db';
import { useLiveQuery } from 'dexie-react-hooks';

function About() {
	const [blob, setBlob] = useState<Blob | null>(null);
	useEffect(() => {
		const fetchBook = async () => {
			const res = await fetch('/見てしまう人びと 幻覚の脳科学.epub');
			const blob = await res.blob();
			setBlob(blob);
		};
		fetchBook();
	}, []);
	const friends = useLiveQuery(() => db.friends.toArray(), []);

	const addFriend = async () => {
		db.friends.add({
			name: 'John Doe',
			age: 30,
			book: blob,
		});
	};

	const deleteFriend = (id: number) => {
		db.friends.delete(id);
	};

	return (
		<>
			<h1>about</h1>
			<Link to='/'>back</Link>
			<Button onClick={addFriend}>Add Friend</Button>
			<ul>
				{friends?.map((friend) => (
					<li key={friend.id}>
						{friend.name} is {friend.age} years old
						{friend.book && (
							<a href={URL.createObjectURL(friend.book as Blob)} download={`${friend.name}_book.epub`}>
								Download Book
							</a>
						)}
						<Button onClick={() => deleteFriend(friend.id)}>Delete</Button>
					</li>
				))}
			</ul>
		</>
	);
}

export default About;
