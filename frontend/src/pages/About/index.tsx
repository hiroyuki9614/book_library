import React from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';

import { db } from '@/features/funcs/db';
import { useLiveQuery } from 'dexie-react-hooks';

function About() {
	const friends = useLiveQuery(() => db.friends.toArray(), []);

	const addFriend = () => {
		db.friends.add({
			name: 'John Doe',
			age: 30,
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
						<Button onClick={() => deleteFriend(friend.id)}>Delete</Button>
					</li>
				))}
			</ul>
		</>
	);
}

export default About;
